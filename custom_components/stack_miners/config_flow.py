"""Config flow for Stack Miners."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.selector import (
    BooleanSelector,
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
    CONF_MINER_PRIORITY,
    CONF_MINER_PRIORITY_INTERNAL,
    CONF_MINERS,
    CONF_MIN_OFF_TIME,
    CONF_MIN_ON_TIME,
    CONF_ROLLING_SAMPLES,
    CONF_SIMULATION,
    CONF_SOC_MIN,
    CONF_SOC_SENSOR,
    DEFAULT_HYSTERESIS_W,
    DEFAULT_MIN_OFF_TIME,
    DEFAULT_MIN_ON_TIME,
    DEFAULT_MINER_POWER_W,
    DEFAULT_ROLLING_SAMPLES,
    DEFAULT_SOC_MIN,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)

_HASS_MINER_DOMAIN = "miner"


def _discover_miner_switches(hass) -> list[dict]:
    """Return all switch entities registered by the hass-miner integration.

    Each entry includes the entity_id, display name, and the current power
    limit reported by the corresponding hass-miner power_limit sensor.
    """
    registry = er.async_get(hass)

    # Build a lookup: config_entry_id -> power_limit entity_id
    power_limit_by_entry: dict[str, str] = {}
    for reg_entry in registry.entities.values():
        if (
            reg_entry.domain == "sensor"
            and reg_entry.platform == _HASS_MINER_DOMAIN
            and reg_entry.unique_id
            and reg_entry.unique_id.endswith("-power_limit")
        ):
            power_limit_by_entry[reg_entry.config_entry_id] = reg_entry.entity_id

    miners = []
    for reg_entry in registry.entities.values():
        if reg_entry.domain == "switch" and reg_entry.platform == _HASS_MINER_DOMAIN:
            name = reg_entry.name or reg_entry.original_name or reg_entry.entity_id

            # Try to read the current power limit for this miner
            power_w = DEFAULT_MINER_POWER_W  # fallback when power_limit sensor is unavailable
            pl_entity_id = power_limit_by_entry.get(reg_entry.config_entry_id)
            if pl_entity_id:
                pl_state = hass.states.get(pl_entity_id)
                if pl_state and pl_state.state not in ("unavailable", "unknown"):
                    try:
                        power_w = int(float(pl_state.state))
                    except ValueError:
                        pass

            miners.append({
                "entity_id": reg_entry.entity_id,
                "name": name,
                "power_w": power_w,
            })

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
            vol.Optional(CONF_SIMULATION, default=defaults.get(CONF_SIMULATION, False)): BooleanSelector(),
            vol.Optional(CONF_SOC_SENSOR, description={"suggested_value": defaults.get(CONF_SOC_SENSOR)}): EntitySelector(
                EntitySelectorConfig(domain="sensor", device_class="battery")
            ),
            vol.Optional(CONF_SOC_MIN, default=defaults.get(CONF_SOC_MIN, DEFAULT_SOC_MIN)): NumberSelector(
                NumberSelectorConfig(min=0, max=100, step=1, unit_of_measurement="%", mode=NumberSelectorMode.SLIDER)
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


def _manual_miners_schema(defaults: list[str] | None = None) -> vol.Schema:
    return vol.Schema(
        {
            vol.Optional("manual_miners", default=defaults or []): EntitySelector(
                EntitySelectorConfig(domain="switch", multiple=True)
            ),
        }
    )


def _miner_schema(name_default: str, idx: int, total: int, power_default: int = DEFAULT_MINER_POWER_W) -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(CONF_MINER_NAME, default=name_default): str,
            vol.Required(CONF_MINER_POWER_W, default=power_default): NumberSelector(
                NumberSelectorConfig(min=1, max=50000, step=10, unit_of_measurement="W", mode=NumberSelectorMode.BOX)
            ),
            vol.Required(CONF_MINER_PRIORITY, default=idx): NumberSelector(
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
        return StackMinersOptionsFlow()

    # Step 1: grid sensor + settings
    async def async_step_user(self, user_input: dict | None = None):
        await self.async_set_unique_id(DOMAIN)
        self._abort_if_unique_id_configured()
        if user_input is not None:
            self._data.update(user_input)
            return await self.async_step_select_miners()
        return self.async_show_form(step_id="user", data_schema=_settings_schema({}))

    # Step 2: select which discovered hass-miner switches to include
    async def async_step_select_miners(self, user_input: dict | None = None):
        discovered = _discover_miner_switches(self.hass)
        self._discovered = {m["entity_id"]: m for m in discovered}

        if not discovered:
            self._selected_ids = []
            return await self.async_step_add_manual_miners()

        options = [{"value": m["entity_id"], "label": m["name"]} for m in discovered]

        if user_input is not None:
            self._selected_ids = user_input["selected_miners"]
            return await self.async_step_add_manual_miners()

        return self.async_show_form(
            step_id="select_miners",
            data_schema=_select_schema(options),
            description_placeholders={"count": str(len(discovered))},
        )

    # Step 3: optionally add further switch entities as miners
    async def async_step_add_manual_miners(self, user_input: dict | None = None):
        if user_input is not None:
            manual_ids: list[str] = user_input.get("manual_miners", [])
            for eid in manual_ids:
                if eid not in self._discovered:
                    self._discovered[eid] = {"entity_id": eid, "name": eid, "power_w": DEFAULT_MINER_POWER_W}

            all_ids = list(self._selected_ids)
            for eid in manual_ids:
                if eid not in all_ids:
                    all_ids.append(eid)

            if not all_ids:
                return self.async_show_form(
                    step_id="add_manual_miners",
                    data_schema=_manual_miners_schema(),
                    errors={"base": "no_miners_selected"},
                )

            self._selected_ids = all_ids
            self._pending = list(all_ids)
            self._miners = []
            return await self.async_step_configure_miner()

        return self.async_show_form(
            step_id="add_manual_miners",
            data_schema=_manual_miners_schema(),
        )

    # Step 3: configure each miner (name, power, priority)
    async def async_step_configure_miner(self, user_input: dict | None = None):
        if not self._pending:
            self._miners.sort(key=lambda m: m[CONF_MINER_PRIORITY_INTERNAL])
            for m in self._miners:
                m.pop(CONF_MINER_PRIORITY_INTERNAL)
            self._data[CONF_MINERS] = self._miners
            return self.async_create_entry(title="Stack Miners", data=self._data)

        entity_id = self._pending[0]
        miner_info = self._discovered.get(entity_id, {})
        name_default = miner_info.get("name", entity_id)
        power_default = miner_info.get("power_w", DEFAULT_MINER_POWER_W)
        idx = len(self._miners) + 1
        total = len(self._selected_ids)

        if user_input is not None:
            self._miners.append(
                {
                    CONF_MINER_NAME: user_input[CONF_MINER_NAME],
                    CONF_MINER_ENTITY_ID: entity_id,
                    CONF_MINER_POWER_W: int(user_input[CONF_MINER_POWER_W]),
                    CONF_MINER_PRIORITY_INTERNAL: int(user_input[CONF_MINER_PRIORITY]),
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


class StackMinersOptionsFlow(config_entries.OptionsFlow):
    """Handle options updates."""

    def __init__(self) -> None:
        self._data: dict[str, Any] = {}
        self._discovered: dict[str, str] = {}
        self._selected_ids: list[str] = []
        self._pending: list[str] = []
        self._miners: list[dict] = []

    async def async_step_init(self, user_input: dict | None = None):
        existing = {**self.config_entry.data, **self.config_entry.options}
        if user_input is not None:
            self._data.update(user_input)
            return await self.async_step_select_miners()
        return self.async_show_form(step_id="init", data_schema=_settings_schema(existing))

    async def async_step_select_miners(self, user_input: dict | None = None):
        existing = {**self.config_entry.data, **self.config_entry.options}
        existing_ids = [m[CONF_MINER_ENTITY_ID] for m in existing.get(CONF_MINERS, [])]

        discovered = _discover_miner_switches(self.hass)
        self._discovered = {m["entity_id"]: m for m in discovered}

        if not discovered:
            self._selected_ids = []
            return await self.async_step_add_manual_miners()

        options = [{"value": m["entity_id"], "label": m["name"]} for m in discovered]
        # Pre-select only IDs that are still in hass-miner discovery
        hass_miner_existing = [eid for eid in existing_ids if eid in self._discovered]

        if user_input is not None:
            self._selected_ids = user_input["selected_miners"]
            return await self.async_step_add_manual_miners()

        return self.async_show_form(
            step_id="select_miners",
            data_schema=_select_schema(options, hass_miner_existing),
            description_placeholders={"count": str(len(discovered))},
        )

    async def async_step_add_manual_miners(self, user_input: dict | None = None):
        existing = {**self.config_entry.data, **self.config_entry.options}
        existing_miners = {m[CONF_MINER_ENTITY_ID]: m for m in existing.get(CONF_MINERS, [])}
        # Pre-populate with previously saved miners that are NOT from hass-miner discovery
        existing_manual_ids = [eid for eid in existing_miners if eid not in self._discovered]

        if user_input is not None:
            manual_ids: list[str] = user_input.get("manual_miners", [])
            for eid in manual_ids:
                if eid not in self._discovered:
                    self._discovered[eid] = {"entity_id": eid, "name": eid, "power_w": DEFAULT_MINER_POWER_W}

            all_ids = list(self._selected_ids)
            for eid in manual_ids:
                if eid not in all_ids:
                    all_ids.append(eid)

            if not all_ids:
                return self.async_show_form(
                    step_id="add_manual_miners",
                    data_schema=_manual_miners_schema(existing_manual_ids),
                    errors={"base": "no_miners_selected"},
                )

            self._selected_ids = all_ids
            self._pending = list(all_ids)
            self._miners = []
            return await self.async_step_configure_miner()

        return self.async_show_form(
            step_id="add_manual_miners",
            data_schema=_manual_miners_schema(existing_manual_ids),
        )

    async def async_step_configure_miner(self, user_input: dict | None = None):
        existing = {**self.config_entry.data, **self.config_entry.options}
        existing_miners = {m[CONF_MINER_ENTITY_ID]: m for m in existing.get(CONF_MINERS, [])}

        if not self._pending:
            self._miners.sort(key=lambda m: m[CONF_MINER_PRIORITY_INTERNAL])
            for m in self._miners:
                m.pop(CONF_MINER_PRIORITY_INTERNAL)
            self._data[CONF_MINERS] = self._miners
            return self.async_create_entry(title="", data=self._data)

        entity_id = self._pending[0]
        miner_info = self._discovered.get(entity_id, {})
        prev = existing_miners.get(entity_id, {})
        name_default = prev.get(CONF_MINER_NAME, miner_info.get("name", entity_id))
        # Prefer previously saved value, then live power_limit sensor, then fallback
        power_default = prev.get(CONF_MINER_POWER_W, miner_info.get("power_w", DEFAULT_MINER_POWER_W))
        idx = len(self._miners) + 1
        total = len(self._selected_ids)

        if user_input is not None:
            self._miners.append(
                {
                    CONF_MINER_NAME: user_input[CONF_MINER_NAME],
                    CONF_MINER_ENTITY_ID: entity_id,
                    CONF_MINER_POWER_W: int(user_input[CONF_MINER_POWER_W]),
                    CONF_MINER_PRIORITY_INTERNAL: int(user_input[CONF_MINER_PRIORITY]),
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
