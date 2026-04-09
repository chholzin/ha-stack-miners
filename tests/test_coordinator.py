"""Tests for StackMinersCoordinator switching logic."""

import asyncio
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

# conftest stubs HA before this import
from custom_components.stack_miners.coordinator import StackMinersCoordinator
from custom_components.stack_miners.const import MODE_IDLE, MODE_RUNNING
from conftest import make_state


def _make_coordinator(hass, entry) -> StackMinersCoordinator:
    return StackMinersCoordinator(hass, entry)


# ---------------------------------------------------------------------------
# Rolling average / grid readings
# ---------------------------------------------------------------------------

class TestRollingBuffer:
    def test_buffer_fills_to_max_samples(self, hass, entry):
        coord = _make_coordinator(hass, entry)
        for v in [100, 200, 300, 400, 500]:
            coord._grid_readings.append(v)
        # rolling_samples=3 → only last 3 kept
        assert list(coord._grid_readings) == [300, 400, 500]

    def test_surplus_is_negated_grid(self, hass, entry):
        coord = _make_coordinator(hass, entry)
        # grid = -2000 W (surplus) → surplus_avg should be +2000
        for _ in range(3):
            coord._grid_readings.append(-2000.0)
        data = coord._build_data()
        assert data["surplus_avg"] == pytest.approx(2000.0)

    def test_surplus_zero_when_consuming(self, hass, entry):
        coord = _make_coordinator(hass, entry)
        for _ in range(3):
            coord._grid_readings.append(500.0)   # consuming 500 W
        data = coord._build_data()
        assert data["surplus_avg"] == pytest.approx(-500.0)


# ---------------------------------------------------------------------------
# Mode
# ---------------------------------------------------------------------------

class TestMode:
    def test_idle_when_no_miners_on(self, hass, entry):
        coord = _make_coordinator(hass, entry)
        coord._miner_states = [False, False]
        coord._update_mode()
        assert coord._mode == MODE_IDLE

    def test_running_when_any_miner_on(self, hass, entry):
        coord = _make_coordinator(hass, entry)
        coord._miner_states = [True, False]
        coord._update_mode()
        assert coord._mode == MODE_RUNNING

    def test_running_when_all_miners_on(self, hass, entry):
        coord = _make_coordinator(hass, entry)
        coord._miner_states = [True, True]
        coord._update_mode()
        assert coord._mode == MODE_RUNNING


# ---------------------------------------------------------------------------
# Switching logic: turn ON
# ---------------------------------------------------------------------------

class TestTurnOn:
    @pytest.mark.asyncio
    async def test_turns_on_first_miner_when_enough_surplus(self, hass, entry):
        """S9 needs 1400 + 100 (hysteresis) = 1500 W surplus to switch on."""
        coord = _make_coordinator(hass, entry)
        coord._miner_states = [False, False]

        # Provide sufficient surplus
        for _ in range(3):
            coord._grid_readings.append(-1600.0)  # surplus = 1600 W

        await coord._evaluate_inner()

        hass.services.async_call.assert_awaited_once_with(
            "homeassistant", "turn_on",
            {"entity_id": "switch.miner_s9_active"},
            blocking=False,
        )
        assert coord._miner_states[0] is True
        assert coord._miner_states[1] is False  # BitAxe not touched yet

    @pytest.mark.asyncio
    async def test_does_not_turn_on_when_surplus_too_low(self, hass, entry):
        """Surplus of 1000 W < 1500 W required → no switch."""
        coord = _make_coordinator(hass, entry)
        coord._miner_states = [False, False]

        for _ in range(3):
            coord._grid_readings.append(-1000.0)

        await coord._evaluate_inner()

        hass.services.async_call.assert_not_awaited()
        assert coord._miner_states == [False, False]

    @pytest.mark.asyncio
    async def test_turns_on_second_miner_when_first_already_on(self, hass, entry):
        """With S9 running (1400W), BitAxe needs 15+100=115 W additional surplus."""
        coord = _make_coordinator(hass, entry)
        coord._miner_states = [True, False]

        # surplus = 1600 W, S9 already running (1400), still 200 W free → BitAxe can start
        for _ in range(3):
            coord._grid_readings.append(-1600.0)

        await coord._evaluate_inner()

        hass.services.async_call.assert_awaited_once_with(
            "homeassistant", "turn_on",
            {"entity_id": "switch.miner_bitaxe_active"},
            blocking=False,
        )
        assert coord._miner_states[1] is True

    @pytest.mark.asyncio
    async def test_only_one_action_per_evaluation(self, hass, entry):
        """Even with massive surplus, only one miner is switched per cycle."""
        coord = _make_coordinator(hass, entry)
        coord._miner_states = [False, False]

        for _ in range(3):
            coord._grid_readings.append(-5000.0)

        await coord._evaluate_inner()

        # Only one service call despite both miners being off
        assert hass.services.async_call.await_count == 1


# ---------------------------------------------------------------------------
# Switching logic: turn OFF
# ---------------------------------------------------------------------------

class TestTurnOff:
    @pytest.mark.asyncio
    async def test_turns_off_last_miner_when_surplus_drops(self, hass, entry):
        """Both miners on, surplus drops to 0 → BitAxe (index 1) turned off first."""
        coord = _make_coordinator(hass, entry)
        coord._miner_states = [True, True]

        # consuming 500 W from grid — not enough for either miner
        for _ in range(3):
            coord._grid_readings.append(500.0)

        await coord._evaluate_inner()

        hass.services.async_call.assert_awaited_once_with(
            "homeassistant", "turn_off",
            {"entity_id": "switch.miner_bitaxe_active"},
            blocking=False,
        )
        assert coord._miner_states[1] is False
        assert coord._miner_states[0] is True  # S9 not touched

    @pytest.mark.asyncio
    async def test_turns_off_first_miner_when_only_one_running(self, hass, entry):
        """Only S9 running, surplus = 0 → S9 turned off."""
        coord = _make_coordinator(hass, entry)
        coord._miner_states = [True, False]

        for _ in range(3):
            coord._grid_readings.append(500.0)

        await coord._evaluate_inner()

        hass.services.async_call.assert_awaited_once_with(
            "homeassistant", "turn_off",
            {"entity_id": "switch.miner_s9_active"},
            blocking=False,
        )

    @pytest.mark.asyncio
    async def test_does_not_turn_off_within_hysteresis(self, hass, entry):
        """S9 is the only running miner, surplus drops to 1350 W.

        turn-off threshold = running_load - S9_power - hysteresis = 1400 - 1400 - 100 = -100.
        surplus (1350) > -100 → stay on. Uses a single-miner entry to avoid the
        coordinator legitimately turning on the BitAxe (only 115 W needed).
        """
        single_entry = MagicMock()
        single_entry.entry_id = "single"
        single_entry.data = {
            "grid_sensor_entity_id": "sensor.netzleistung_median",
            "hysteresis_w": 100,
            "rolling_samples": 3,
            "min_on_time_s": 0,
            "min_off_time_s": 0,
            "miners": [
                {"name": "S9", "entity_id": "switch.miner_s9_active", "power_w": 1400},
            ],
        }
        single_entry.options = {}

        coord = _make_coordinator(hass, single_entry)
        coord._miner_states = [True]

        for _ in range(3):
            coord._grid_readings.append(-1350.0)

        await coord._evaluate_inner()

        hass.services.async_call.assert_not_awaited()


# ---------------------------------------------------------------------------
# Hysteresis band
# ---------------------------------------------------------------------------

class TestHysteresis:
    @pytest.mark.asyncio
    async def test_turn_on_requires_extra_buffer(self, hass, entry):
        """Surplus exactly equals miner power but below hysteresis → no turn-on."""
        coord = _make_coordinator(hass, entry)
        coord._miner_states = [False, False]

        # surplus = 1400 W exactly (S9 power), hysteresis = 100 → need 1500 W
        for _ in range(3):
            coord._grid_readings.append(-1400.0)

        await coord._evaluate_inner()

        hass.services.async_call.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_turn_on_at_exact_threshold(self, hass, entry):
        """Surplus = 1500 W (1400 + 100 hysteresis) → turn on."""
        coord = _make_coordinator(hass, entry)
        coord._miner_states = [False, False]

        for _ in range(3):
            coord._grid_readings.append(-1500.0)

        await coord._evaluate_inner()

        hass.services.async_call.assert_awaited_once()


# ---------------------------------------------------------------------------
# Minimum on/off time
# ---------------------------------------------------------------------------

class TestMinTimes:
    @pytest.mark.asyncio
    async def test_min_off_time_blocks_turn_on(self, hass, entry):
        """Miner was just switched off → min_off_time blocks turn-on."""
        entry.data = {**entry.data, "min_off_time_s": 60, "min_on_time_s": 0}
        coord = _make_coordinator(hass, entry)
        coord._miner_states = [False, False]
        # Record a very recent switch time
        coord._last_switch_time[0] = datetime.now(tz=timezone.utc)

        for _ in range(3):
            coord._grid_readings.append(-2000.0)

        await coord._evaluate_inner()

        hass.services.async_call.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_min_on_time_blocks_turn_off(self, hass, entry):
        """Miner was just switched on → min_on_time blocks turn-off."""
        entry.data = {**entry.data, "min_on_time_s": 60, "min_off_time_s": 0}
        coord = _make_coordinator(hass, entry)
        coord._miner_states = [True, False]
        coord._last_switch_time[0] = datetime.now(tz=timezone.utc)

        for _ in range(3):
            coord._grid_readings.append(500.0)  # not enough surplus

        await coord._evaluate_inner()

        hass.services.async_call.assert_not_awaited()


# ---------------------------------------------------------------------------
# build_data output
# ---------------------------------------------------------------------------

class TestBuildData:
    def test_active_miners_count(self, hass, entry):
        coord = _make_coordinator(hass, entry)
        coord._miner_states = [True, False]
        data = coord._build_data()
        assert data["active_miners"] == 1
        assert data["total_miners"] == 2

    def test_active_power_falls_back_to_configured(self, hass, entry):
        """No consumption sensors wired → falls back to configured power_w."""
        coord = _make_coordinator(hass, entry)
        coord._miner_states = [True, True]
        # consumption sensors not set (None) → fallback to 1400 + 15
        data = coord._build_data()
        assert data["active_power_w"] == 1415

    def test_active_power_uses_real_sensor(self, hass, entry):
        """When consumption sensor is available its value is used."""
        coord = _make_coordinator(hass, entry)
        coord._miner_states = [True, False]
        coord._consumption_sensor_ids[0] = "sensor.miner_s9_consumption"

        real_state = MagicMock()
        real_state.state = "1320"  # real wattage differs from configured 1400
        hass.states.get.side_effect = lambda eid: real_state if eid == "sensor.miner_s9_consumption" else MagicMock(state="off")

        data = coord._build_data()
        assert data["active_power_w"] == pytest.approx(1320.0)

    def test_active_power_falls_back_when_sensor_unavailable(self, hass, entry):
        """Unavailable consumption sensor → configured power_w is used."""
        coord = _make_coordinator(hass, entry)
        coord._miner_states = [True, False]
        coord._consumption_sensor_ids[0] = "sensor.miner_s9_consumption"

        unavail = MagicMock()
        unavail.state = "unavailable"
        hass.states.get.side_effect = lambda eid: unavail if eid == "sensor.miner_s9_consumption" else MagicMock(state="off")

        data = coord._build_data()
        assert data["active_power_w"] == pytest.approx(1400.0)

    def test_hashrate_sums_active_miners(self, hass, entry):
        """Total hashrate sums real sensor values for active miners only."""
        coord = _make_coordinator(hass, entry)
        coord._miner_states = [True, True]
        coord._hashrate_sensor_ids[0] = "sensor.miner_s9_hashrate"
        coord._hashrate_sensor_ids[1] = "sensor.miner_bitaxe_hashrate"

        def _fake_state(eid):
            return {"sensor.miner_s9_hashrate": "14.5", "sensor.miner_bitaxe_hashrate": "0.5"}.get(eid, "off")

        real = MagicMock()
        real.state = "x"  # overridden per entity below
        hass.states.get.side_effect = lambda eid: make_state(_fake_state(eid))

        data = coord._build_data()
        assert data["total_hashrate_th"] == pytest.approx(15.0)

    def test_hashrate_excludes_inactive_miners(self, hass, entry):
        """Inactive miner hashrate is not included in the sum."""
        coord = _make_coordinator(hass, entry)
        coord._miner_states = [True, False]
        coord._hashrate_sensor_ids[0] = "sensor.miner_s9_hashrate"
        coord._hashrate_sensor_ids[1] = "sensor.miner_bitaxe_hashrate"

        hass.states.get.side_effect = lambda eid: make_state(
            "14.5" if eid == "sensor.miner_s9_hashrate" else "0.5"
        )

        data = coord._build_data()
        assert data["total_hashrate_th"] == pytest.approx(14.5)

    def test_hashrate_zero_when_no_sensors(self, hass, entry):
        """No hashrate sensors wired → total is 0."""
        coord = _make_coordinator(hass, entry)
        coord._miner_states = [True, True]
        # hashrate_sensor_ids stay None
        data = coord._build_data()
        assert data["total_hashrate_th"] == 0.0

    def test_enabled_flag(self, hass, entry):
        coord = _make_coordinator(hass, entry)
        coord.disable()
        assert coord._build_data()["enabled"] is False
        coord.enable()
        assert coord._build_data()["enabled"] is True


# ---------------------------------------------------------------------------
# Simulation mode
# ---------------------------------------------------------------------------

class TestSimulation:
    @pytest.mark.asyncio
    async def test_simulation_uses_slider_value(self, hass, entry):
        """When simulation is active, coordinator uses simulated surplus."""
        coord = _make_coordinator(hass, entry)
        coord._miner_states = [False, False]
        coord.set_simulation_active(True)
        coord.set_simulation_surplus(1600.0)  # enough for S9 (1400 + 100)

        await coord._evaluate_inner()

        hass.services.async_call.assert_awaited_once_with(
            "homeassistant", "turn_on",
            {"entity_id": "switch.miner_s9_active"},
            blocking=False,
        )

    @pytest.mark.asyncio
    async def test_simulation_ignores_grid_readings(self, hass, entry):
        """Real grid readings are ignored when simulation is active."""
        coord = _make_coordinator(hass, entry)
        coord._miner_states = [False, False]
        coord.set_simulation_active(True)
        coord.set_simulation_surplus(0.0)  # no surplus in simulation

        # Real grid shows massive surplus — must be ignored
        for _ in range(3):
            coord._grid_readings.append(-9000.0)

        await coord._evaluate_inner()

        hass.services.async_call.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_simulation_off_uses_real_sensor(self, hass, entry):
        """With simulation off, real grid readings drive decisions."""
        coord = _make_coordinator(hass, entry)
        coord._miner_states = [False, False]
        coord.set_simulation_active(False)

        for _ in range(3):
            coord._grid_readings.append(-1600.0)

        await coord._evaluate_inner()

        hass.services.async_call.assert_awaited_once()

    def test_simulation_state_in_build_data(self, hass, entry):
        coord = _make_coordinator(hass, entry)
        coord.set_simulation_active(True)
        coord.set_simulation_surplus(2500.0)
        data = coord._build_data()
        assert data["simulation_active"] is True
        assert data["simulation_surplus_w"] == pytest.approx(2500.0)


# ---------------------------------------------------------------------------
# Disabled state
# ---------------------------------------------------------------------------

class TestEnabled:
    @pytest.mark.asyncio
    async def test_disabled_coordinator_does_not_switch(self, hass, entry):
        """Grid events must not trigger switching when the coordinator is disabled."""
        coord = _make_coordinator(hass, entry)
        coord._miner_states = [False, False]
        coord.disable()

        for _ in range(3):
            coord._grid_readings.append(-5000.0)

        # Replicate what _handle_grid_state_change does: only schedule when enabled
        if coord._enabled and not coord._evaluating and not coord._simulation_active:
            await coord._evaluate_inner()

        hass.services.async_call.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_re_enabled_coordinator_switches_again(self, hass, entry):
        """After re-enabling, the coordinator resumes switching normally."""
        coord = _make_coordinator(hass, entry)
        coord._miner_states = [False, False]
        coord.disable()
        coord.enable()

        for _ in range(3):
            coord._grid_readings.append(-1600.0)

        await coord._evaluate_inner()

        hass.services.async_call.assert_awaited_once()


# ---------------------------------------------------------------------------
# Miner state synchronisation (restart / manual override)
# ---------------------------------------------------------------------------

class TestMinerStateSync:
    def _make_event(self, entity_id: str, state: str) -> MagicMock:
        event = MagicMock()
        event.data = {"entity_id": entity_id, "new_state": make_state(state)}
        return event

    def test_syncs_on_state_to_true(self, hass, entry):
        """When hass-miner reports a switch as 'on', _miner_states is updated."""
        coord = _make_coordinator(hass, entry)
        coord._miner_states = [False, False]

        coord._handle_miner_state_change(self._make_event("switch.miner_s9_active", "on"))

        assert coord._miner_states[0] is True
        assert coord._miner_states[1] is False

    def test_syncs_off_state_to_false(self, hass, entry):
        """When hass-miner reports a switch as 'off', _miner_states is updated."""
        coord = _make_coordinator(hass, entry)
        coord._miner_states = [True, True]

        coord._handle_miner_state_change(self._make_event("switch.miner_bitaxe_active", "off"))

        assert coord._miner_states[0] is True
        assert coord._miner_states[1] is False

    def test_ignores_unavailable_state(self, hass, entry):
        """Unavailable state must not reset optimistic tracking."""
        coord = _make_coordinator(hass, entry)
        coord._miner_states = [True, False]

        coord._handle_miner_state_change(self._make_event("switch.miner_s9_active", "unavailable"))

        assert coord._miner_states[0] is True  # unchanged

    def test_ignores_unknown_state(self, hass, entry):
        """Unknown state must not reset optimistic tracking."""
        coord = _make_coordinator(hass, entry)
        coord._miner_states = [True, False]

        coord._handle_miner_state_change(self._make_event("switch.miner_s9_active", "unknown"))

        assert coord._miner_states[0] is True  # unchanged

    def test_updates_mode_after_sync(self, hass, entry):
        """Mode is recalculated after a state sync."""
        from custom_components.stack_miners.const import MODE_RUNNING, MODE_IDLE
        coord = _make_coordinator(hass, entry)
        coord._miner_states = [False, False]

        coord._handle_miner_state_change(self._make_event("switch.miner_s9_active", "on"))

        assert coord._mode == MODE_RUNNING

    def test_ignores_unknown_entity(self, hass, entry):
        """Events for unmanaged entities are silently ignored."""
        coord = _make_coordinator(hass, entry)
        coord._miner_states = [False, False]

        coord._handle_miner_state_change(self._make_event("switch.some_other_switch", "on"))

        assert coord._miner_states == [False, False]


# ---------------------------------------------------------------------------
# Unavailable miner handling (_switch_miner guard)
# ---------------------------------------------------------------------------

class TestUnavailableMiner:
    @pytest.mark.asyncio
    async def test_switch_skipped_when_unavailable(self, hass, entry):
        """No service call and no optimistic update when miner entity is unavailable."""
        coord = _make_coordinator(hass, entry)
        coord._miner_states = [True, False]

        hass.states.get.return_value = make_state("unavailable")

        await coord._switch_miner(0, turn_on=False)

        hass.services.async_call.assert_not_awaited()
        # _miner_states must be unchanged — we do not flip it optimistically
        assert coord._miner_states[0] is True

    @pytest.mark.asyncio
    async def test_switch_skipped_when_unknown(self, hass, entry):
        """No service call and no optimistic update when miner entity is unknown."""
        coord = _make_coordinator(hass, entry)
        coord._miner_states = [False, False]

        hass.states.get.return_value = make_state("unknown")

        await coord._switch_miner(0, turn_on=True)

        hass.services.async_call.assert_not_awaited()
        assert coord._miner_states[0] is False

    @pytest.mark.asyncio
    async def test_switch_proceeds_when_available(self, hass, entry):
        """Service call and optimistic update happen normally for available entities."""
        coord = _make_coordinator(hass, entry)
        coord._miner_states = [False, False]

        hass.states.get.return_value = make_state("off")

        await coord._switch_miner(0, turn_on=True)

        hass.services.async_call.assert_awaited_once()
        assert coord._miner_states[0] is True
