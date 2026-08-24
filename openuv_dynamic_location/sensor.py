"""Sensor platform for OpenUV Dynamic Location."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory, UnitOfLength
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import DynamicLocationData
from .const import DOMAIN, SIGNAL_LOCATION_UPDATED


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the diagnostic sensors for a config entry."""

    data: DynamicLocationData = hass.data[DOMAIN][entry.entry_id]

    async_add_entities(
        [
            OpenUvLocationDistanceSensor(entry, data),
            OpenUvLocationLastUpdatedSensor(entry, data),
        ]
    )


class _OpenUvLocationBaseSensor(SensorEntity):
    """Shared behaviour for the diagnostic sensors.

    Both sensors are pushed via the same dispatcher signal instead of
    polling, since a new value is only ever available right after a
    GPS check has run.
    """

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_should_poll = False

    def __init__(
        self,
        entry: ConfigEntry,
        data: DynamicLocationData,
        unique_id_suffix: str,
    ) -> None:
        """Initialize the sensor."""

        self._entry = entry
        self._data = data
        self._attr_unique_id = f"{entry.entry_id}_{unique_id_suffix}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.title,
            entry_type=DeviceEntryType.SERVICE,
        )

    async def async_added_to_hass(self) -> None:
        """Subscribe to location update signals."""

        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                f"{SIGNAL_LOCATION_UPDATED}_{self._entry.entry_id}",
                self._handle_update,
            )
        )

    @callback
    def _handle_update(self) -> None:
        """Handle a location update signal."""

        self.async_write_ha_state()


class OpenUvLocationDistanceSensor(_OpenUvLocationBaseSensor):
    """Distance between the current GPS fix and the applied location.

    This exists mainly for diagnostics: it lets you see, from
    Developer Tools or a dashboard, how close the tracked GPS source
    is to crossing the configured movement threshold, and what
    location and GPS entities this entry is currently using.
    """

    _attr_translation_key = "distance_since_update"
    _attr_device_class = SensorDeviceClass.DISTANCE
    _attr_native_unit_of_measurement = UnitOfLength.KILOMETERS
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(
        self,
        entry: ConfigEntry,
        data: DynamicLocationData,
    ) -> None:
        """Initialize the sensor."""

        super().__init__(entry, data, "distance_since_update")

    @property
    def native_value(self) -> float | None:
        """Return the last measured distance in kilometres."""

        if self._data.last_distance_km is None:
            return None

        return round(self._data.last_distance_km, 3)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return diagnostic attributes."""

        return {
            "latitude_entity": self._data.latitude_entity,
            "longitude_entity": self._data.longitude_entity,
            "last_applied_latitude": self._data.last_latitude,
            "last_applied_longitude": self._data.last_longitude,
            "distance_threshold_km": self._data.distance_km,
            "openuv_entry_id": self._data.openuv_entry_id,
        }


class OpenUvLocationLastUpdatedSensor(_OpenUvLocationBaseSensor):
    """When OpenUV's location was last changed by this entry.

    This is set the moment a GPS-triggered update is successfully
    applied and OpenUV is reloaded - not merely when a GPS check
    runs, so it always reflects the age of the currently configured
    OpenUV location.
    """

    _attr_translation_key = "last_updated"
    _attr_device_class = SensorDeviceClass.TIMESTAMP

    def __init__(
        self,
        entry: ConfigEntry,
        data: DynamicLocationData,
    ) -> None:
        """Initialize the sensor."""

        super().__init__(entry, data, "last_updated")

    @property
    def native_value(self) -> datetime | None:
        """Return the timestamp of the last applied location."""

        return self._data.last_updated
