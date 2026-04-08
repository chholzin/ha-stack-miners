"""StackMiners coordinator — event-driven surplus power controller."""

from __future__ import annotations

import asyncio
import logging
import statistics
from collections import deque
from datetime import datetime, timezone
from typing import Any

from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

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
    CONF_SIMULATION,
    DOMAIN,
    MODE_IDLE,
    MODE_RUNNING,
)

_LOGGER = logging.getLogger(__name__)

_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


class StackMinersCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Manages surplus-power-based miner switching."""

    def __init__(self, hass: HomeAssistant, entry) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass=hass,
            logger=_LOGGER,
            name=DOMAIN,
            # No automatic polling — we are event-driven
            update_interval=None,
        )
        self._entry = entry
        data = {**entry.data, **entry.options}

        self._grid_sensor: str = data[CONF_GRID_SENSOR]
        self._miners: list[dict] = list(data.get(CONF_MINERS, []))
        self._hysteresis_w: float = float(data.get(CONF_HYSTERESIS_W, 100))
        self._rolling_samples: int = int(data.get(CONF_ROLLING_SAMPLES, 5))
        self._min_on_time: float = float(data.get(CONF_MIN_ON_TIME, 60))
        self._min_off_time: float = float(data.get(CONF_MIN_OFF_TIME, 60))

        self._grid_readings: deque[float] = deque(maxlen=self._rolling_samples)
        # Optimistic tracking: True = miner is ON (or commanded ON)
        self._miner_states: list[bool] = [False] * len(self._miners)
        # Last time a switch command was issued for each miner
        self._last_switch_time: list[datetime] = [_EPOCH] * len(self._miners)

        self._enabled: bool = True
        self._mode: str = MODE_IDLE
        self._unsubscribe_grid = None
        self._evaluating: bool = False
        # entity_id of hass-miner's miner_consumption / hashrate sensor per miner
        self._consumption_sensor_ids: list[str | None] = [None] * len(self._miners)
        self._hashrate_sensor_ids: list[str | None] = [None] * len(self._miners)

        # Simulation
        self._simulation_enabled: bool = bool(data.get(CONF_SIMULATION, False))
        self._simulation_active: bool = False   # runtime toggle (the switch entity)
        self._simulation_surplus_w: float = 0.0  # runtime value (the number entity)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def async_start(self) -> None:
        """Register state listeners and seed initial miner states."""
        # Seed miner states from current HA state
        for i, miner in enumerate(self._miners):
            state = self.hass.states.get(miner[CONF_MINER_ENTITY_ID])
            if state and state.state not in (STATE_UNAVAILABLE, STATE_UNKNOWN, "unknown"):
                self._miner_states[i] = state.state == "on"

        # Find the hass-miner miner_consumption sensor for each miner via entity registry
        registry = er.async_get(self.hass)
        # Build lookups: config_entry_id -> sensor entity_id
        consumption_by_entry: dict[str, str] = {}
        hashrate_by_entry: dict[str, str] = {}
        for reg in registry.entities.values():
            if reg.domain == "sensor" and reg.platform == "miner" and reg.unique_id:
                if reg.unique_id.endswith("-miner_consumption"):
                    consumption_by_entry[reg.config_entry_id] = reg.entity_id
                elif reg.unique_id.endswith("-hashrate"):
                    hashrate_by_entry[reg.config_entry_id] = reg.entity_id

        # Match each miner switch to its sensors via shared config_entry_id
        for i, miner in enumerate(self._miners):
            switch_reg = registry.async_get(miner[CONF_MINER_ENTITY_ID])
            if switch_reg:
                entry_id = switch_reg.config_entry_id
                self._consumption_sensor_ids[i] = consumption_by_entry.get(entry_id)
                self._hashrate_sensor_ids[i] = hashrate_by_entry.get(entry_id)
                _LOGGER.debug(
                    "Miner '%s' → consumption: %s, hashrate: %s",
                    miner[CONF_MINER_NAME],
                    self._consumption_sensor_ids[i],
                    self._hashrate_sensor_ids[i],
                )

        # Register grid sensor listener
        self._unsubscribe_grid = async_track_state_change_event(
            self.hass,
            [self._grid_sensor],
            self._handle_grid_state_change,
        )

        # Seed one reading from current grid sensor state
        grid_state = self.hass.states.get(self._grid_sensor)
        if grid_state and grid_state.state not in (STATE_UNAVAILABLE, STATE_UNKNOWN):
            try:
                self._grid_readings.append(float(grid_state.state))
            except ValueError:
                pass

        self.async_set_updated_data(self._build_data())
        _LOGGER.debug("StackMinersCoordinator started, grid sensor: %s", self._grid_sensor)

    async def async_shutdown(self) -> None:
        """Unregister listeners."""
        if self._unsubscribe_grid:
            self._unsubscribe_grid()
            self._unsubscribe_grid = None
        await super().async_shutdown()

    # ------------------------------------------------------------------
    # Master enable switch
    # ------------------------------------------------------------------

    def enable(self) -> None:
        """Enable automated miner control."""
        self._enabled = True
        self.async_set_updated_data(self._build_data())

    def disable(self) -> None:
        """Disable automated miner control (leaves miners as-is)."""
        self._enabled = False
        self.async_set_updated_data(self._build_data())

    # ------------------------------------------------------------------
    # Simulation controls (called by switch/number entities)
    # ------------------------------------------------------------------

    def set_simulation_active(self, active: bool) -> None:
        """Enable or disable simulation mode at runtime."""
        self._simulation_active = active
        self.async_set_updated_data(self._build_data())
        if self._enabled and active:
            self.hass.async_create_task(self._evaluate())

    def set_simulation_surplus(self, watts: float) -> None:
        """Update the simulated surplus value and re-evaluate."""
        self._simulation_surplus_w = watts
        self.async_set_updated_data(self._build_data())
        if self._enabled and self._simulation_active:
            self.hass.async_create_task(self._evaluate())

    # ------------------------------------------------------------------
    # Grid state listener
    # ------------------------------------------------------------------

    @callback
    def _handle_grid_state_change(self, event: Event) -> None:
        """Handle a new reading from the grid power sensor."""
        new_state = event.data.get("new_state")
        if new_state is None or new_state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
            return
        try:
            value = float(new_state.state)
        except ValueError:
            return

        self._grid_readings.append(value)
        # Push updated grid reading to sensor entities immediately
        self.async_set_updated_data(self._build_data())

        # Skip evaluation when simulation is active — simulation drives decisions
        if self._enabled and not self._evaluating and not self._simulation_active:
            self.hass.async_create_task(self._evaluate())

    # ------------------------------------------------------------------
    # Switching logic
    # ------------------------------------------------------------------

    async def _evaluate(self) -> None:
        """Evaluate whether any miner should be switched on or off."""
        if self._evaluating:
            return
        self._evaluating = True
        try:
            await self._evaluate_inner()
        finally:
            self._evaluating = False

    async def _evaluate_inner(self) -> None:
        # Determine effective surplus
        if self._simulation_active:
            surplus_w = self._simulation_surplus_w
        else:
            if len(self._grid_readings) < 1:
                return
            # Rolling average: negative grid = surplus feeding to grid
            avg_grid = statistics.mean(self._grid_readings)
            surplus_w = -avg_grid

        now = datetime.now(tz=timezone.utc)

        # --- Try to turn ON the next miner (lowest-index not-yet-running) ---
        for i, miner in enumerate(self._miners):
            if self._miner_states[i]:
                continue  # already on
            needed = miner[CONF_MINER_POWER_W] + self._hysteresis_w
            if surplus_w >= needed:
                elapsed = (now - self._last_switch_time[i]).total_seconds()
                if elapsed >= self._min_off_time:
                    await self._switch_miner(i, turn_on=True)
                    return  # one action per evaluation cycle
            # Miners are priority-ordered; if we can't power this one, stop
            break

        # --- Try to turn OFF the highest-index running miner (reverse order) ---
        running_load = sum(
            self._miners[j][CONF_MINER_POWER_W]
            for j in range(len(self._miners))
            if self._miner_states[j]
        )

        for i in reversed(range(len(self._miners))):
            if not self._miner_states[i]:
                continue  # already off
            miner_power = self._miners[i][CONF_MINER_POWER_W]
            # Threshold: surplus required to keep this miner running
            threshold = running_load - miner_power - self._hysteresis_w
            if surplus_w < threshold:
                elapsed = (now - self._last_switch_time[i]).total_seconds()
                if elapsed >= self._min_on_time:
                    await self._switch_miner(i, turn_on=False)
                    return  # one action per evaluation cycle
            break

    async def _switch_miner(self, index: int, *, turn_on: bool) -> None:
        """Call HA service to turn a miner switch on or off."""
        miner = self._miners[index]
        entity_id = miner[CONF_MINER_ENTITY_ID]
        name = miner[CONF_MINER_NAME]
        service = "turn_on" if turn_on else "turn_off"

        # Verify entity exists
        state = self.hass.states.get(entity_id)
        if state is None:
            _LOGGER.warning("Miner entity %s not found, skipping", entity_id)
            return

        _LOGGER.info(
            "StackMiners: %s miner '%s' (%s)", "Enabling" if turn_on else "Disabling", name, entity_id
        )

        # Optimistic update before the service call
        self._miner_states[index] = turn_on
        self._last_switch_time[index] = datetime.now(tz=timezone.utc)
        self._update_mode()
        self.async_set_updated_data(self._build_data())

        try:
            await self.hass.services.async_call(
                "homeassistant",
                service,
                {"entity_id": entity_id},
                blocking=False,
            )
        except Exception as err:
            _LOGGER.error("Failed to %s miner '%s': %s", service, name, err)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _update_mode(self) -> None:
        self._mode = MODE_RUNNING if any(self._miner_states) else MODE_IDLE

    def _read_sensor_float(self, entity_id: str | None) -> float | None:
        """Read a numeric sensor state; return None if unavailable."""
        if entity_id is None:
            return None
        state = self.hass.states.get(entity_id)
        if state and state.state not in (STATE_UNAVAILABLE, STATE_UNKNOWN):
            try:
                return float(state.state)
            except ValueError:
                pass
        return None

    def _real_consumption_w(self, index: int) -> float:
        """Return actual power draw; falls back to configured power_w."""
        v = self._read_sensor_float(self._consumption_sensor_ids[index])
        return v if v is not None else float(self._miners[index][CONF_MINER_POWER_W])

    def _build_data(self) -> dict[str, Any]:
        readings = list(self._grid_readings)
        grid_power = readings[-1] if readings else None
        surplus_avg = -statistics.mean(readings) if readings else None
        active_count = sum(self._miner_states)
        active_power = sum(
            self._real_consumption_w(i)
            for i in range(len(self._miners))
            if self._miner_states[i]
        )
        total_hashrate = sum(
            v
            for i in range(len(self._miners))
            if self._miner_states[i]
            for v in [self._read_sensor_float(self._hashrate_sensor_ids[i])]
            if v is not None
        )
        return {
            "grid_power": grid_power,
            "surplus_avg": surplus_avg,
            "active_miners": active_count,
            "active_power_w": active_power,
            "mode": self._mode,
            "enabled": self._enabled,
            "miner_states": list(self._miner_states),
            "total_miners": len(self._miners),
            "total_hashrate_th": round(total_hashrate, 2),
            "simulation_enabled": self._simulation_enabled,
            "simulation_active": self._simulation_active,
            "simulation_surplus_w": self._simulation_surplus_w,
        }
