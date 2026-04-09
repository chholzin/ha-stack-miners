"""StackMiners coordinator — event-driven surplus power controller."""

from __future__ import annotations

import asyncio
import logging
import statistics
from collections import deque
from datetime import datetime, timezone
from typing import Any

from homeassistant.config_entries import ConfigEntry
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
    CONF_SOC_MIN,
    CONF_SOC_SENSOR,
    DEFAULT_SOC_MIN,
    DOMAIN,
    MODE_IDLE,
    MODE_RUNNING,
    MODE_SOC_PROTECTION,
)

_LOGGER = logging.getLogger(__name__)

# Sentinel "never switched" — far enough in the past that any min-time check passes.
_NEVER = datetime(1970, 1, 1, tzinfo=timezone.utc)


class StackMinersCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Manages surplus-power-based miner switching."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
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
        self._last_switch_time: list[datetime] = [_NEVER] * len(self._miners)

        self._enabled: bool = True
        self._mode: str = MODE_IDLE
        self._unsubscribe_grid = None
        self._unsubscribe_miners = None
        self._evaluating: bool = False
        # entity_id of hass-miner's miner_consumption / hashrate sensor per miner
        self._consumption_sensor_ids: list[str | None] = [None] * len(self._miners)
        self._hashrate_sensor_ids: list[str | None] = [None] * len(self._miners)

        # Battery SOC protection (both optional — None means feature disabled)
        soc_sensor = data.get(CONF_SOC_SENSOR) or None  # coerce "" to None
        self._soc_sensor: str | None = soc_sensor
        self._soc_min: float = float(data.get(CONF_SOC_MIN, DEFAULT_SOC_MIN))

        # Simulation
        self._simulation_enabled: bool = bool(data.get(CONF_SIMULATION, False))
        self._simulation_active: bool = False   # runtime toggle (the switch entity)
        self._simulation_surplus_w: float = 0.0  # runtime value (the number entity)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def async_start(self) -> None:
        """Register state listeners and seed initial miner states."""
        self._seed_miner_states()
        self._discover_related_sensors(er.async_get(self.hass))

        self._unsubscribe_grid = async_track_state_change_event(
            self.hass,
            [self._grid_sensor],
            self._handle_grid_state_change,
        )

        # Track miner switch states so _miner_states stays in sync after HA
        # restarts (hass-miner may come online after us) and after manual overrides.
        miner_entity_ids = [m[CONF_MINER_ENTITY_ID] for m in self._miners]
        if miner_entity_ids:
            self._unsubscribe_miners = async_track_state_change_event(
                self.hass,
                miner_entity_ids,
                self._handle_miner_state_change,
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
        if self._unsubscribe_miners:
            self._unsubscribe_miners()
            self._unsubscribe_miners = None
        await super().async_shutdown()

    def _seed_miner_states(self) -> None:
        """Initialise _miner_states from current HA switch states."""
        for i, miner in enumerate(self._miners):
            state = self.hass.states.get(miner[CONF_MINER_ENTITY_ID])
            if state and state.state not in (STATE_UNAVAILABLE, STATE_UNKNOWN, "unknown"):
                self._miner_states[i] = state.state == "on"

    def _discover_related_sensors(self, registry: er.EntityRegistry) -> None:
        """Populate consumption and hashrate sensor IDs via the entity registry.

        Each hass-miner switch shares a config_entry_id with its associated
        sensors.  We look up the switch in the registry, then use that
        config_entry_id to find the matching sensor entities.
        """
        consumption_by_entry: dict[str, str] = {}
        hashrate_by_entry: dict[str, str] = {}
        for reg in registry.entities.values():
            if reg.domain == "sensor" and reg.platform == "miner" and reg.unique_id:
                if reg.unique_id.endswith("-miner_consumption"):
                    consumption_by_entry[reg.config_entry_id] = reg.entity_id
                elif reg.unique_id.endswith("-hashrate"):
                    hashrate_by_entry[reg.config_entry_id] = reg.entity_id

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
            self._schedule_evaluate()

    def set_simulation_surplus(self, watts: float) -> None:
        """Update the simulated surplus value and re-evaluate."""
        self._simulation_surplus_w = watts
        self.async_set_updated_data(self._build_data())
        if self._enabled and self._simulation_active:
            self._schedule_evaluate()

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
            self._schedule_evaluate()

    @callback
    def _handle_miner_state_change(self, event: Event) -> None:
        """Keep _miner_states in sync with the real HA switch state.

        Called when hass-miner reports a state change for any of the managed
        miner switches — e.g. after HA restarts and hass-miner comes online,
        or when a miner is toggled manually via the HA UI.
        Unavailable / unknown states are intentionally ignored so that a
        temporary disconnect does not reset our optimistic tracking.
        """
        entity_id = event.data.get("entity_id")
        new_state = event.data.get("new_state")
        if new_state is None or new_state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
            return

        for i, miner in enumerate(self._miners):
            if miner[CONF_MINER_ENTITY_ID] == entity_id:
                self._miner_states[i] = new_state.state == "on"
                self._update_mode()
                self.async_set_updated_data(self._build_data())
                break

    # ------------------------------------------------------------------
    # Switching logic
    # ------------------------------------------------------------------

    def _schedule_evaluate(self) -> None:
        """Schedule a single evaluation cycle on the event loop."""
        self.hass.async_create_task(self._evaluate())

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
        """Run one turn-on / turn-off decision cycle."""
        if self._soc_below_threshold():
            await self._shutdown_all_miners()
            return

        surplus_w = self._effective_surplus()
        if surplus_w is None:
            return

        now = datetime.now(tz=timezone.utc)

        if await self._try_turn_on(surplus_w, now):
            return  # one action per evaluation cycle
        await self._try_turn_off(surplus_w, now)

    def _soc_below_threshold(self) -> bool:
        """Return True if the battery SOC sensor is configured and below the minimum."""
        if self._soc_sensor is None:
            return False
        soc = self._read_sensor_float(self._soc_sensor)
        if soc is None:
            return False  # sensor unavailable — don't shut down on uncertainty
        return soc < self._soc_min

    async def _shutdown_all_miners(self) -> None:
        """Turn off every running miner immediately (SOC protection).

        Bypasses min_on_time — battery protection takes priority over hardware
        wear protection.  Entity-unavailability is still respected via
        _switch_miner's guard.
        """
        for i, miner in enumerate(self._miners):
            if self._miner_states[i]:
                _LOGGER.warning(
                    "SOC below %.0f%% — shutting down miner '%s'",
                    self._soc_min,
                    miner[CONF_MINER_NAME],
                )
                await self._switch_miner(i, turn_on=False)

    def _effective_surplus(self) -> float | None:
        """Return the surplus power to base decisions on.

        Returns the simulated value when simulation is active, otherwise the
        rolling average of real grid readings (None if no readings yet).
        """
        if self._simulation_active:
            return self._simulation_surplus_w
        if not self._grid_readings:
            return None
        return -statistics.mean(self._grid_readings)

    def _is_entity_reachable(self, entity_id: str) -> bool:
        """Return True if the entity exists and reports a known (non-unavailable) state."""
        state = self.hass.states.get(entity_id)
        return state is not None and state.state not in (STATE_UNAVAILABLE, STATE_UNKNOWN)

    async def _try_turn_on(self, surplus_w: float, now: datetime) -> bool:
        """Try to turn on the next miner in priority order.

        Returns True if a miner was switched on (caller should stop).

        Unavailable miners are skipped so that a lower-priority reachable
        miner can still be started.  A reachable but under-powered miner
        (insufficient surplus or still in min_off_time) stops the search —
        we never leapfrog a waiting miner that could run soon.
        """
        for i, miner in enumerate(self._miners):
            if self._miner_states[i]:
                continue  # already on; look for the next off miner

            if not self._is_entity_reachable(miner[CONF_MINER_ENTITY_ID]):
                continue  # offline — skip, lower-priority miners may still start

            needed = miner[CONF_MINER_POWER_W] + self._hysteresis_w
            if surplus_w >= needed:
                elapsed = (now - self._last_switch_time[i]).total_seconds()
                if elapsed >= self._min_off_time:
                    await self._switch_miner(i, turn_on=True)
                    return True
            # Reachable but blocked (insufficient surplus or min_off_time) — stop.
            # Lower-priority miners wait until this one can run.
            break

        return False

    async def _try_turn_off(self, surplus_w: float, now: datetime) -> None:
        """Try to turn off the lowest-priority running miner.

        Only the lowest-priority reachable running miner is evaluated per
        cycle.  Unavailable miners are skipped so that a higher-priority
        reachable miner can still be turned off if needed.
        """
        running_load = sum(
            self._miners[j][CONF_MINER_POWER_W]
            for j in range(len(self._miners))
            if self._miner_states[j]
        )

        for i in reversed(range(len(self._miners))):
            if not self._miner_states[i]:
                continue  # already off

            if not self._is_entity_reachable(self._miners[i][CONF_MINER_ENTITY_ID]):
                continue  # offline — skip, check next-higher-priority running miner

            miner_power = self._miners[i][CONF_MINER_POWER_W]
            # Turn off if surplus can no longer sustain the remaining load after removal
            threshold = running_load - miner_power - self._hysteresis_w
            if surplus_w < threshold:
                elapsed = (now - self._last_switch_time[i]).total_seconds()
                if elapsed >= self._min_on_time:
                    await self._switch_miner(i, turn_on=False)
            # Only act on the single lowest-priority reachable running miner per cycle
            break

    async def _switch_miner(self, index: int, *, turn_on: bool) -> None:
        """Call HA service to turn a miner switch on or off."""
        miner = self._miners[index]
        entity_id = miner[CONF_MINER_ENTITY_ID]
        name = miner[CONF_MINER_NAME]
        service = "turn_on" if turn_on else "turn_off"

        state = self.hass.states.get(entity_id)
        if state is None:
            _LOGGER.warning("Miner '%s' (%s) not found in HA state machine, skipping", name, entity_id)
            return
        if state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
            _LOGGER.warning(
                "Miner '%s' (%s) is %s — skipping switch command, keeping current tracked state",
                name, entity_id, state.state,
            )
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

    def _current_mode(self) -> str:
        if self._soc_below_threshold():
            return MODE_SOC_PROTECTION
        return MODE_RUNNING if any(self._miner_states) else MODE_IDLE

    def _update_mode(self) -> None:
        self._mode = self._current_mode()

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
            "mode": self._current_mode(),
            "enabled": self._enabled,
            "soc": self._read_sensor_float(self._soc_sensor),
            "soc_min": self._soc_min if self._soc_sensor else None,
            "miner_states": list(self._miner_states),
            "total_miners": len(self._miners),
            "total_hashrate_th": round(total_hashrate, 2),
            "simulation_enabled": self._simulation_enabled,
            "simulation_active": self._simulation_active,
            "simulation_surplus_w": self._simulation_surplus_w,
        }
