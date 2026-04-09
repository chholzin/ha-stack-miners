"""Shared helpers for Stack Miners entities."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.entity import DeviceInfo

from .const import DOMAIN


def device_info(entry: ConfigEntry) -> DeviceInfo:
    """Return the shared DeviceInfo for all Stack Miners entities."""
    return DeviceInfo(
        identifiers={(DOMAIN, entry.entry_id)},
        name="Stack Miners",
        manufacturer="Custom",
        model="Solar Miner Controller",
    )
