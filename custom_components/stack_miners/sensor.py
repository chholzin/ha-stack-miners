"""Sensor entities for Miner Stack."""

from __future__ import annotations

from homeassistant.components.sensor import (
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfPower
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import StackMinersCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Miner Stack sensor entities."""
    coordinator: StackMinersCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        [
            GridPowerSensor(coordinator, entry),
            SurplusPowerSensor(coordinator, entry),
            ActiveMinersSensor(coordinator, entry),
            ActivePowerSensor(coordinator, entry),
            TotalHashrateSensor(coordinator, entry),
            ModeStateSensor(coordinator, entry),
        ]
    )


def _device_info(entry: ConfigEntry) -> DeviceInfo:
    return DeviceInfo(
        identifiers={(DOMAIN, entry.entry_id)},
        name="Miner Stack",
        manufacturer="Custom",
        model="Solar Miner Controller",
    )


class _BaseSensor(CoordinatorEntity[StackMinersCoordinator], SensorEntity):
    _attr_has_entity_name = True

    def __init__(self, coordinator: StackMinersCoordinator, entry: ConfigEntry, key: str) -> None:
        super().__init__(coordinator)
        self._key = key
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_device_info = _device_info(entry)

    @property
    def available(self) -> bool:
        return self.coordinator.data is not None


class GridPowerSensor(_BaseSensor):
    """Current grid power reading (W). Negative = surplus."""

    _attr_native_unit_of_measurement = UnitOfPower.WATT
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:transmission-tower"
    _attr_translation_key = "grid_power"

    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry, "grid_power")

    @property
    def native_value(self):
        if self.coordinator.data is None:
            return None
        v = self.coordinator.data.get("grid_power")
        return round(v, 1) if v is not None else None


class SurplusPowerSensor(_BaseSensor):
    """Rolling-average surplus power (W). Positive = available excess."""

    _attr_native_unit_of_measurement = UnitOfPower.WATT
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:solar-power"
    _attr_translation_key = "surplus_power"

    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry, "surplus_power")

    @property
    def native_value(self):
        if self.coordinator.data is None:
            return None
        v = self.coordinator.data.get("surplus_avg")
        return round(v, 1) if v is not None else None


class ActiveMinersSensor(_BaseSensor):
    """Number of miners currently running."""

    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:pickaxe"
    _attr_translation_key = "active_miners"

    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry, "active_miners")

    @property
    def native_value(self):
        if self.coordinator.data is None:
            return None
        return self.coordinator.data.get("active_miners", 0)

    @property
    def extra_state_attributes(self):
        if self.coordinator.data is None:
            return {}
        return {"total_miners": self.coordinator.data.get("total_miners", 0)}


class ActivePowerSensor(_BaseSensor):
    """Sum of power consumed by currently active miners (W)."""

    _attr_native_unit_of_measurement = UnitOfPower.WATT
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:lightning-bolt"
    _attr_translation_key = "active_power"

    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry, "active_power")

    @property
    def native_value(self):
        if self.coordinator.data is None:
            return None
        return self.coordinator.data.get("active_power_w", 0)


class TotalHashrateSensor(_BaseSensor):
    """Sum of real hashrate across all active miners (TH/s)."""

    _attr_native_unit_of_measurement = "TH/s"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:lightning-bolt-circle"
    _attr_translation_key = "total_hashrate"

    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry, "total_hashrate")

    @property
    def native_value(self):
        if self.coordinator.data is None:
            return None
        return self.coordinator.data.get("total_hashrate_th", 0.0)


class ModeStateSensor(_BaseSensor):
    """Current controller mode: idle / running / ramping_up / ramping_down."""

    _attr_icon = "mdi:state-machine"
    _attr_translation_key = "mode"

    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry, "mode")

    @property
    def native_value(self):
        if self.coordinator.data is None:
            return None
        return self.coordinator.data.get("mode", "idle")
