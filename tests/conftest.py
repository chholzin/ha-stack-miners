"""Minimal HA stubs so coordinator tests run without homeassistant installed."""

import asyncio
import sys
import types
from unittest.mock import AsyncMock, MagicMock

import pytest


# ---------------------------------------------------------------------------
# Stub the entire homeassistant package tree before any project imports
# ---------------------------------------------------------------------------

def _mod(name: str):
    m = types.ModuleType(name)
    sys.modules[name] = m
    return m


def _stub_ha():
    ha          = _mod("homeassistant")
    cfg         = _mod("homeassistant.config_entries")
    core        = _mod("homeassistant.core")
    const       = _mod("homeassistant.const")
    helpers     = _mod("homeassistant.helpers")
    ev          = _mod("homeassistant.helpers.event")
    upd         = _mod("homeassistant.helpers.update_coordinator")
    er          = _mod("homeassistant.helpers.entity_registry")
    ent         = _mod("homeassistant.helpers.entity")
    plat        = _mod("homeassistant.helpers.entity_platform")
    sel         = _mod("homeassistant.helpers.selector")
    comp        = _mod("homeassistant.components")
    sensor_comp = _mod("homeassistant.components.sensor")
    switch_comp = _mod("homeassistant.components.switch")

    # --- homeassistant.const ---
    const.STATE_UNAVAILABLE = "unavailable"
    const.STATE_UNKNOWN     = "unknown"

    class _Platform:
        NUMBER = "number"
        SENSOR = "sensor"
        SWITCH = "switch"
    const.Platform = _Platform

    class _UoP:
        WATT = "W"
    const.UnitOfPower = _UoP

    # --- homeassistant.core ---
    core.callback    = lambda f: f          # passthrough decorator
    core.HomeAssistant = MagicMock
    core.Event         = MagicMock

    # --- homeassistant.config_entries ---
    class _ConfigEntry:
        pass

    class _ConfigFlow:
        def __init_subclass__(cls, domain=None, **kwargs):
            super().__init_subclass__(**kwargs)

    class _OptionsFlow:
        """Stub for OptionsFlow — exposes config_entry like modern HA."""
        config_entry: "_ConfigEntry | None" = None

    cfg.ConfigEntry  = _ConfigEntry
    cfg.ConfigFlow   = _ConfigFlow
    cfg.OptionsFlow  = _OptionsFlow
    cfg.HANDLERS     = {}

    # --- homeassistant.helpers.event ---
    ev.async_track_state_change_event = MagicMock(return_value=lambda: None)

    # --- homeassistant.helpers.update_coordinator ---
    class _DataUpdateCoordinatorMeta(type):
        """Allow DataUpdateCoordinator[X] subscript syntax."""
        def __getitem__(cls, item):
            return cls

    class _DataUpdateCoordinator(metaclass=_DataUpdateCoordinatorMeta):
        def __init__(self, hass=None, logger=None, name=None, update_interval=None, **kw):
            self.hass   = hass
            self.logger = logger
            self.name   = name
            self.data   = None

        def async_set_updated_data(self, data):
            self.data = data

        async def async_config_entry_first_refresh(self):
            pass

    class _CoordinatorEntity:
        def __init__(self, coordinator):
            self.coordinator = coordinator

    upd.DataUpdateCoordinator = _DataUpdateCoordinator
    upd.CoordinatorEntity     = _CoordinatorEntity
    upd.UpdateFailed          = Exception

    # --- homeassistant.helpers.entity ---
    ent.DeviceInfo = dict

    # --- homeassistant.helpers.entity_platform ---
    plat.AddEntitiesCallback = object

    # --- homeassistant.helpers.entity_registry ---
    er.async_get = MagicMock(return_value=MagicMock())

    # --- components ---
    sensor_comp.SensorEntity = object
    class _SSC:
        MEASUREMENT = "measurement"
    sensor_comp.SensorStateClass = _SSC
    switch_comp.SwitchEntity = object

    # --- selectors (stubbed as passthrough validators so voluptuous accepts them) ---
    class _SimpleSelector:
        """Passthrough stub — voluptuous calls it as a validator; we return value as-is."""
        def __init__(self, *args, **kwargs):
            pass
        def __call__(self, value):
            return value

    class _NumberSelectorMode:
        BOX    = "box"
        SLIDER = "slider"

    class _SelectSelectorMode:
        LIST = "list"

    for _name in (
        "BooleanSelector",
        "EntitySelector", "EntitySelectorConfig",
        "NumberSelector", "NumberSelectorConfig",
        "SelectSelector", "SelectSelectorConfig",
    ):
        setattr(sel, _name, _SimpleSelector)
    sel.NumberSelectorMode = _NumberSelectorMode
    sel.SelectSelectorMode = _SelectSelectorMode


_stub_ha()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def make_state(state: str = "off") -> MagicMock:
    """Return a minimal HA state mock with the given state string."""
    s = MagicMock()
    s.state = state
    return s


@pytest.fixture
def hass():
    """Minimal HomeAssistant mock.

    states.get() returns a valid (off) state by default so the coordinator
    doesn't skip switch calls due to a missing entity.
    """
    h = MagicMock()
    h.states.get.return_value = make_state("off")
    h.services.async_call = AsyncMock()
    h.async_create_task = lambda coro: asyncio.ensure_future(coro)
    return h


@pytest.fixture
def entry():
    """Minimal ConfigEntry mock — two miners, min times = 0 for fast tests."""
    e = MagicMock()
    e.entry_id = "test_entry"
    e.data = {
        "grid_sensor_entity_id": "sensor.netzleistung_median",
        "hysteresis_w": 100,
        "rolling_samples": 3,
        "min_on_time_s": 0,
        "min_off_time_s": 0,
        "miners": [
            {"name": "S9",     "entity_id": "switch.miner_s9_active",     "power_w": 1400},
            {"name": "BitAxe", "entity_id": "switch.miner_bitaxe_active", "power_w": 15},
        ],
    }
    e.options = {}
    return e
