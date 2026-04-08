"""Config flow for Stack Miners."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.selector import (
    EntitySelector,
    EntitySelectorConfig,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
)

from .const import (
    CONF_GRID_SENSOR,
    CONF_HYSTERESIS_W,
    CONF_MINER_ENTITY_ID,
    CONF_MINER_NAME,
    CONF_MINER_POWER_W,
    CONF_MINERS,
    CONF_MIN_OFF_TIME,
    CONF_MIN_ON_TIME,
    CONF_ROLLING_SAMPLES,
    DEFAULT_HYSTERESIS_W,
    DEFAULT_MIN_OFF_TIME,
    DEFAULT_MIN_ON_TIME,
    DEFAULT_ROLLING_SAMPLES,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)

_HASS_MINER_DOMAIN = "miner"


def _discover_miner_switches(hass) -> list[dict[str, str]]:
    """Return all switch entities registered by the hass-miner integration."""
    registry = er.async_get(hass)
    miners = []
    for entry in registry.entities.values():
        if entry.domain == "switch" and entry.platform == _HASS_MINER_DOMAIN:
            name = entry.name or entry.original_name or entry.entity_id
            miners.append({"entity_id": entry.entity_id, "name": name})
    return sorted(miners, key=lambda m: m["name"])


def _settings_schema(defaults: dict) -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(CONF_GRID_SENSOR, default=defaults.get(CONF_GRID_SENSOR, "")): EntitySelector(
                EntitySelectorConfig(domain="sensor")
            ),
            vol.Optional(CONF_HYSTERESIS_W, default=defaults.get(CONF_HYSTERESIS_W, DEFAULT_HYSTERESIS_W)): NumberSelector(
                NumberSelectorConfig(min=0, max=5000, step=10, unit_of_measurement="W", mode=NumberSelectorMode.BOX)
            ),
            vol.Optional(CONF_ROLLING_SAMPLES, default=defaults.get(CONF_ROLLING_SAMPLES, DEFAULT_ROLLING_SAMPLES)): NumberSelector(
                NumberSelectorConfig(min=1, max=60, step=1, mode=NumberSelectorMode.BOX)
            ),
            vol.Optional(CONF_MIN_ON_TIME, default=defaults.get(CONF_MIN_ON_TIME, DEFAULT_MIN_ON_TIME)): NumberSelector(
                NumberSelectorConfig(min=5, max=3600, step=5, unit_of_measurement="s", mode=NumberSelectorMode.BOX)
            ),
            vol.Optional(CONF_MIN_OFF_TIME, default=defaults.get(CONF_MIN_OFF_TIME, DEFAULT_MIN_OFF_TIME)): NumberSelector(
                NumberSelectorConfig(min=5, max=3600, step=5, unit_of_measurement="s", mode=NumberSelectorMode.BOX)
            ),
        }
    )


def _select_schema(options: list[dict], defaults: list[str] | None = None) -> vol.Schema:
    return vol.Schema(
        {
            vol.Required("selected_miners", default=defaults or []): SelectSelector(
                SelectSelectorConfig(
                    options=options,
                    multiple=True,
                    mode=SelectSelectorMode.LIST,
                )
            ),
        }
    )


def _miner_schema(name_default: str, idx: int, total: int, power_default: int = 1000) -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(CONF_MINER_NAME, default=name_default): str,
            vol.Required(CONF_MINER_POWER_W, default=power_default): NumberSelector(
                NumberSelectorConfig(min=1, max=50000, step=10, unit_of_measurement="W", mode=NumberSelectorMode.BOX)
            ),
            vol.Required("priority", default=idx): NumberSelector(
                NumberSelectorConfig(min=1, max=total, step=1, mode=NumberSelectorMode.BOX)
            ),
        }
    )


class StackMinersConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Stack Miners."""

    VERSION = 1

    def __init__(self) -> None:
        self._data: dict[str, Any] = {}
        self._discovered: dict[str, str] = {}   # entity_id -> name
        self._selected_ids: list[str] = []
        self._pending: list[str] = []
        self._miners: list[dict] = []

    @staticmethod
    def async_get_options_flow(entry: config_entries.ConfigEntry) -> StackMinersOptionsFlow:
        return StackMinersOptionsFlow(entry)

    # Step 1: grid sensor + settings
    async def async_step_user(self, user_input: dict | None = None):
        if user_input is not None:
            self._data.update(user_input)
            return await self.async_step_select_miners()
        return self.async_show_form(step_id="user", data_schema=_settings_schema({}))

    # Step 2: select which discovered miners to include
    async def async_step_select_miners(self, user_input: dict | None = None):
        discovered = _discover_miner_switches(self.hass)
        if not discovered:
            return self.async_abort(reason="no_miners_found")

        self._discovered = {m["entity_id"]: m["name"] for m in discovered}
        options = [{"value": m["entity_id"], "label": m["name"]} for m in discovered]

        if user_input is not None:
            self._selected_ids = user_input["selected_miners"]
            if not self._selected_ids:
                return self.async_show_form(
                    step_id="select_miners",
                    data_schema=_select_schema(options),
                    errors={"base": "no_miners_selected"},
                )
            self._pending = list(self._selected_ids)
            self._miners = []
            return await self.async_step_configure_miner()

        return self.async_show_form(
            step_id="select_miners",
            data_schema=_select_schema(options),
            description_placeholders={"count": str(len(discovered))},
        )

    # Step 3: configure each miner (name, power, priority)
    async def async_step_configure_miner(self, user_input: dict | None = None):
        if not self._pending:
            self._miners.sort(key=lambda m: m["_priority"])
            for m in self._miners:
                m.pop("_priority")
            self._data[CONF_MINERS] = self._miners
            return self.async_create_entry(title="Stack Miners", data=self._data)

        entity_id = self._pending[0]
        name_default = self._discovered.get(entity_id, entity_id)
        idx = len(self._miners) + 1
        total = len(self._selected_ids)

        if user_input is not None:
            self._miners.append(
                {
                    CONF_MINER_NAME: user_input[CONF_MINER_NAME],
                    CONF_MINER_ENTITY_ID: entity_id,
                    CONF_MINER_POWER_W: int(user_input[CONF_MINER_POWER_W]),
                    "_priority": int(user_input["priority"]),
                }
            )
            self._pending.pop(0)
            return await self.async_step_configure_miner()

        return self.async_show_form(
            step_id="configure_miner",
            data_schema=_miner_schema(name_default, idx, total),
            description_placeholders={
                "count": str(idx),
                "total": str(total),
                "entity_id": entity_id,
            },
        )


class StackMinersOptionsFlow(config_entries.OptionsFlow):
    """Handle options updates."""

    def __init__(self, entry: config_entries.ConfigEntry) -> None:
        self._entry = entry
        self._data: dict[str, Any] = {}
        self._discovered: dict[str, str] = {}
        self._selected_ids: list[str] = []
        self._pending: list[str] = []
        self._miners: list[dict] = []

    async def async_step_init(self, user_input: dict | None = None):
        existing = {**self._entry.data, **self._entry.options}
        if user_input is not None:
            self._data.update(user_input)
            return await self.async_step_select_miners()
        return self.async_show_form(step_id="init", data_schema=_settings_schema(existing))

    async def async_step_select_miners(self, user_input: dict | None = None):
        existing = {**self._entry.data, **self._entry.options}
        existing_ids = [m[CONF_MINER_ENTITY_ID] for m in existing.get(CONF_MINERS, [])]

        discovered = _discover_miner_switches(self.hass)
        if not discovered:
            return self.async_abort(reason="no_miners_found")

        self._discovered = {m["entity_id"]: m["name"] for m in discovered}
        options = [{"value": m["entity_id"], "label": m["name"]} for m in discovered]

        if user_input is not None:
            self._selected_ids = user_input["selected_miners"]
            if not self._selected_ids:
                return self.async_show_form(
                    step_id="select_miners",
                    data_schema=_select_schema(options, existing_ids),
                    errors={"base": "no_miners_selected"},
                )
            self._pending = list(self._selected_ids)
            self._miners = []
            return await self.async_step_configure_miner()

        return self.async_show_form(
            step_id="select_miners",
            data_schema=_select_schema(options, existing_ids),
            description_placeholders={"count": str(len(discovered))},
        )

    async def async_step_configure_miner(self, user_input: dict | None = None):
        existing = {**self._entry.data, **self._entry.options}
        existing_miners = {m[CONF_MINER_ENTITY_ID]: m for m in existing.get(CONF_MINERS, [])}

        if not self._pending:
            self._miners.sort(key=lambda m: m["_priority"])
            for m in self._miners:
                m.pop("_priority")
            self._data[CONF_MINERS] = self._miners
            return self.async_create_entry(title="", data=self._data)

        entity_id = self._pending[0]
        prev = existing_miners.get(entity_id, {})
        name_default = prev.get(CONF_MINER_NAME, self._discovered.get(entity_id, entity_id))
        power_default = prev.get(CONF_MINER_POWER_W, 1000)
        idx = len(self._miners) + 1
        total = len(self._selected_ids)

        if user_input is not None:
            self._miners.append(
                {
                    CONF_MINER_NAME: user_input[CONF_MINER_NAME],
                    CONF_MINER_ENTITY_ID: entity_id,
                    CONF_MINER_POWER_W: int(user_input[CONF_MINER_POWER_W]),
                    "_priority": int(user_input["priority"]),
                }
            )
            self._pending.pop(0)
            return await self.async_step_configure_miner()

        return self.async_show_form(
            step_id="configure_miner",
            data_schema=_miner_schema(name_default, idx, total, power_default),
            description_placeholders={
                "count": str(idx),
                "total": str(total),
                "entity_id": entity_id,
            },
        )
