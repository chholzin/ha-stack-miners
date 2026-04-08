"""Switch entities for Stack Miners."""

from __future__ import annotations

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_SIMULATION, DOMAIN
from .coordinator import StackMinersCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Stack Miners switch entities."""
    coordinator: StackMinersCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities = [StackMinersEnabledSwitch(coordinator, entry)]

    data = {**entry.data, **entry.options}
    if data.get(CONF_SIMULATION):
        entities.append(StackMinersSimulationSwitch(coordinator, entry))

    async_add_entities(entities)


class StackMinersEnabledSwitch(CoordinatorEntity[StackMinersCoordinator], SwitchEntity):
    """Master on/off for the automatic miner stack controller."""

    _attr_has_entity_name = True
    _attr_icon = "mdi:power"
    _attr_translation_key = "enabled"

    def __init__(self, coordinator: StackMinersCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_enabled"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name="Miner Stack",
            manufacturer="Custom",
            model="Solar Miner Controller",
        )

    @property
    def is_on(self) -> bool:
        if self.coordinator.data is None:
            return True
        return self.coordinator.data.get("enabled", True)

    async def async_turn_on(self, **kwargs) -> None:
        self.coordinator.enable()

    async def async_turn_off(self, **kwargs) -> None:
        self.coordinator.disable()


def _device_info(entry: ConfigEntry) -> DeviceInfo:
    return DeviceInfo(
        identifiers={(DOMAIN, entry.entry_id)},
        name="Miner Stack",
        manufacturer="Custom",
        model="Solar Miner Controller",
    )


class StackMinersSimulationSwitch(CoordinatorEntity[StackMinersCoordinator], SwitchEntity):
    """Switch to activate/deactivate surplus simulation mode."""

    _attr_has_entity_name = True
    _attr_icon = "mdi:sine-wave"
    _attr_translation_key = "simulation"

    def __init__(self, coordinator: StackMinersCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_simulation"
        self._attr_device_info = _device_info(entry)

    @property
    def is_on(self) -> bool:
        if self.coordinator.data is None:
            return False
        return self.coordinator.data.get("simulation_active", False)

    async def async_turn_on(self, **kwargs) -> None:
        self.coordinator.set_simulation_active(True)

    async def async_turn_off(self, **kwargs) -> None:
        self.coordinator.set_simulation_active(False)
