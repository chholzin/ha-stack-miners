"""Number entity for Stack Miners — simulation surplus slider."""

from __future__ import annotations

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfPower
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_SIMULATION, DOMAIN
from .coordinator import StackMinersCoordinator
from .helpers import device_info


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Stack Miners number entities."""
    data = {**entry.data, **entry.options}
    if not data.get(CONF_SIMULATION):
        return

    coordinator: StackMinersCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([StackMinersSimulationSurplus(coordinator, entry)])


class StackMinersSimulationSurplus(CoordinatorEntity[StackMinersCoordinator], NumberEntity):
    """Slider to set the simulated PV surplus power (0–10000 W)."""

    _attr_has_entity_name = True
    _attr_icon = "mdi:solar-power-variant"
    _attr_translation_key = "simulation_surplus"
    _attr_native_min_value = 0
    _attr_native_max_value = 10000
    _attr_native_step = 10
    _attr_native_unit_of_measurement = UnitOfPower.WATT
    _attr_mode = NumberMode.SLIDER

    def __init__(self, coordinator: StackMinersCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_simulation_surplus"
        self._attr_device_info = device_info(entry)

    @property
    def native_value(self) -> float:
        if self.coordinator.data is None:
            return 0.0
        return self.coordinator.data.get("simulation_surplus_w", 0.0)

    async def async_set_native_value(self, value: float) -> None:
        self.coordinator.set_simulation_surplus(value)
