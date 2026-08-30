"""OpenUV Dynamic Location integration."""

from __future__ import annotations

import asyncio
import logging
import math
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_LATITUDE, CONF_LONGITUDE, Platform
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_call_later, async_track_state_change_event
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util
from homeassistant.util.location import distance as ha_distance

from .const import (
    CONF_DEBOUNCE_SECONDS,
    CONF_DISTANCE_KM,
    CONF_LATITUDE_ENTITY,
    CONF_LONGITUDE_ENTITY,
    CONF_OPENUV_ENTRY_ID,
    CONFIG_ENTRY_VERSION,
    DEFAULT_DEBOUNCE_SECONDS,
    DEFAULT_DISTANCE_KM,
    DEFAULT_LATITUDE_ENTITY,
    DEFAULT_LONGITUDE_ENTITY,
    OPENUV_DOMAIN,
    SIGNAL_LOCATION_UPDATED,
    STORAGE_KEY,
    STORAGE_VERSION,
)

_LOGGER = logging.getLogger(__name__)
PLATFORMS: list[Platform] = [Platform.SENSOR]


@dataclass
class DynamicLocationData:
    """Runtime state for a config entry."""

    store: Store
    latitude_entity: str
    longitude_entity: str
    distance_km: float
    debounce_seconds: float
    openuv_entry_id: str | None
    last_latitude: float | None = None
    last_longitude: float | None = None
    update_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    debounce_cancel: Any = None
    last_distance_km: float | None = None
    last_updated: datetime | None = None


async def async_setup(hass: HomeAssistant, config: dict[str, Any]) -> bool:
    """Set up the integration."""

    return True


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Migrate older entries to schema v3."""

    if entry.version >= CONFIG_ENTRY_VERSION:
        return True

    merged = {**entry.data, **entry.options}
    target_id = merged.get(CONF_OPENUV_ENTRY_ID)
    if target_id is None:
        targets = hass.config_entries.async_entries(OPENUV_DOMAIN)
        if targets:
            target_id = targets[0].entry_id

    new_data = {
        CONF_LATITUDE_ENTITY: merged.get(
            CONF_LATITUDE_ENTITY, DEFAULT_LATITUDE_ENTITY
        ),
        CONF_LONGITUDE_ENTITY: merged.get(
            CONF_LONGITUDE_ENTITY, DEFAULT_LONGITUDE_ENTITY
        ),
    }
    if target_id is not None:
        new_data[CONF_OPENUV_ENTRY_ID] = target_id

    new_options = {
        CONF_DISTANCE_KM: float(
            merged.get(CONF_DISTANCE_KM, DEFAULT_DISTANCE_KM)
        ),
        CONF_DEBOUNCE_SECONDS: float(
            merged.get(CONF_DEBOUNCE_SECONDS, DEFAULT_DEBOUNCE_SECONDS)
        ),
    }

    hass.config_entries.async_update_entry(
        entry,
        data=new_data,
        options=new_options,
        unique_id=target_id,
        version=CONFIG_ENTRY_VERSION,
    )
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up a config entry."""

    store = Store[dict[str, Any]](
        hass, STORAGE_VERSION, f"{STORAGE_KEY}_{entry.entry_id}"
    )
    stored = await store.async_load() or {}
    stored_updated = stored.get("updated")

    data = DynamicLocationData(
        store=store,
        last_latitude=stored.get("latitude"),
        last_longitude=stored.get("longitude"),
        last_updated=(
            dt_util.parse_datetime(stored_updated) if stored_updated else None
        ),
        latitude_entity=entry.data.get(
            CONF_LATITUDE_ENTITY, DEFAULT_LATITUDE_ENTITY
        ),
        longitude_entity=entry.data.get(
            CONF_LONGITUDE_ENTITY, DEFAULT_LONGITUDE_ENTITY
        ),
        distance_km=float(
            entry.options.get(CONF_DISTANCE_KM, DEFAULT_DISTANCE_KM)
        ),
        debounce_seconds=float(
            entry.options.get(
                CONF_DEBOUNCE_SECONDS, DEFAULT_DEBOUNCE_SECONDS
            )
        ),
        openuv_entry_id=entry.data.get(CONF_OPENUV_ENTRY_ID),
    )
    entry.runtime_data = data

    target = _resolve_target_entry(hass, data)
    if target is not None:
        target_latitude, target_longitude = _get_coordinates(target)
        if target_latitude is not None and target_longitude is not None:
            _cleanup_stale_openuv_registry_entries(
                hass, target, target_latitude, target_longitude
            )
            _repair_openuv_entry_metadata(
                hass, target, target_latitude, target_longitude
            )

    @callback
    def gps_changed(event: Event) -> None:
        """Handle a GPS sensor state change."""

        _schedule_location_check(hass, entry, data)

    entry.async_on_unload(
        async_track_state_change_event(
            hass,
            [data.latitude_entity, data.longitude_entity],
            gps_changed,
        )
    )
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))
    _schedule_location_check(hass, entry, data, delay=0)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""

    unload_ok = await hass.config_entries.async_unload_platforms(
        entry, PLATFORMS
    )
    if not unload_ok:
        return False

    data: DynamicLocationData = entry.runtime_data
    if data.debounce_cancel is not None:
        data.debounce_cancel()
        data.debounce_cancel = None
    return True


async def _async_options_updated(
    hass: HomeAssistant, entry: ConfigEntry
) -> None:
    """Reload the integration after options change."""

    await hass.config_entries.async_reload(entry.entry_id)


@callback
def _schedule_location_check(
    hass: HomeAssistant,
    entry: ConfigEntry,
    data: DynamicLocationData,
    delay: float | None = None,
) -> None:
    """Schedule a debounced location check."""

    if data.debounce_cancel is not None:
        data.debounce_cancel()
    data.debounce_cancel = async_call_later(
        hass,
        data.debounce_seconds if delay is None else delay,
        lambda _: hass.create_task(_async_check_location(hass, entry, data)),
    )


async def _async_check_location(
    hass: HomeAssistant,
    entry: ConfigEntry,
    data: DynamicLocationData,
) -> None:
    """Update OpenUV when movement exceeds the threshold."""

    data.debounce_cancel = None
    async with data.update_lock:
        latitude_state = hass.states.get(data.latitude_entity)
        longitude_state = hass.states.get(data.longitude_entity)
        if latitude_state is None or longitude_state is None:
            _LOGGER.debug("GPS entities are not available yet")
            return
        if latitude_state.state in ("unknown", "unavailable") or longitude_state.state in (
            "unknown",
            "unavailable",
        ):
            return

        try:
            latitude = float(latitude_state.state)
            longitude = float(longitude_state.state)
        except (TypeError, ValueError):
            _LOGGER.warning(
                "Invalid GPS coordinates: latitude=%s longitude=%s",
                latitude_state.state,
                longitude_state.state,
            )
            return

        if not _valid_coordinates(latitude, longitude):
            _LOGGER.warning(
                "GPS coordinates outside valid range: latitude=%.6f longitude=%.6f",
                latitude,
                longitude,
            )
            return

        target = _resolve_target_entry(hass, data)
        if target is None:
            _LOGGER.error("Configured OpenUV entry is unavailable")
            return

        reference_latitude = data.last_latitude
        reference_longitude = data.last_longitude
        if reference_latitude is None or reference_longitude is None:
            reference_latitude, reference_longitude = _get_coordinates(target)
            if reference_latitude is None or reference_longitude is None:
                await _save_reference(
                    data, latitude, longitude, mark_updated=False
                )
                return
            await _save_reference(
                data,
                reference_latitude,
                reference_longitude,
                mark_updated=False,
            )

        distance_km = _distance_km(
            reference_latitude,
            reference_longitude,
            latitude,
            longitude,
        )
        data.last_distance_km = distance_km
        async_dispatcher_send(
            hass, f"{SIGNAL_LOCATION_UPDATED}_{entry.entry_id}"
        )
        if distance_km <= data.distance_km:
            return

        current_latitude, current_longitude = _get_coordinates(target)
        if current_latitude == latitude and current_longitude == longitude:
            await _save_reference(
                data, latitude, longitude, mark_updated=False
            )
            return

        if current_latitude is None or current_longitude is None:
            _LOGGER.error("Configured OpenUV entry has invalid coordinates")
            return

        new_unique_id = _openuv_config_unique_id(latitude, longitude)
        if _openuv_unique_id_conflicts(hass, target, new_unique_id):
            _LOGGER.error(
                "Cannot move OpenUV to %s because another OpenUV entry "
                "already uses those coordinates",
                new_unique_id,
            )
            return

        old_data = dict(target.data)
        old_unique_id = target.unique_id
        old_title = target.title
        old_default_title = _openuv_config_unique_id(
            current_latitude, current_longitude
        )
        new_title = (
            new_unique_id
            if target.title in (target.unique_id, old_default_title)
            else target.title
        )

        try:
            unload_success = await hass.config_entries.async_unload(
                target.entry_id
            )
        except Exception:
            _LOGGER.exception(
                "Exception while unloading OpenUV before changing its location"
            )
            return
        if not unload_success:
            _LOGGER.error("OpenUV unload failed before changing location")
            return

        _migrate_openuv_registry_location(
            hass,
            target,
            current_latitude,
            current_longitude,
            latitude,
            longitude,
        )

        new_data = dict(target.data)
        new_data[CONF_LATITUDE] = latitude
        new_data[CONF_LONGITUDE] = longitude
        hass.config_entries.async_update_entry(
            target,
            data=new_data,
            unique_id=new_unique_id,
            title=new_title,
        )

        try:
            setup_success = await hass.config_entries.async_setup(
                target.entry_id
            )
        except Exception:
            _LOGGER.exception(
                "Exception while setting up OpenUV after changing its location"
            )
            setup_success = False

        if not setup_success:
            _LOGGER.error(
                "OpenUV setup failed after changing location; restoring "
                "the previous location"
            )
            _migrate_openuv_registry_location(
                hass,
                target,
                latitude,
                longitude,
                current_latitude,
                current_longitude,
            )
            hass.config_entries.async_update_entry(
                target,
                data=old_data,
                unique_id=old_unique_id,
                title=old_title,
            )
            try:
                await hass.config_entries.async_setup(target.entry_id)
            except Exception:
                _LOGGER.exception(
                    "Exception while restoring OpenUV after a failed "
                    "location update"
                )
            return

        _cleanup_stale_openuv_registry_entries(
            hass, target, latitude, longitude
        )
        await _save_reference(data, latitude, longitude, mark_updated=True)
        async_dispatcher_send(
            hass, f"{SIGNAL_LOCATION_UPDATED}_{entry.entry_id}"
        )


async def _save_reference(
    data: DynamicLocationData,
    latitude: float,
    longitude: float,
    *,
    mark_updated: bool,
) -> None:
    """Persist a movement reference and optionally mark a real update."""

    data.last_latitude = latitude
    data.last_longitude = longitude
    data.last_distance_km = 0.0
    if mark_updated:
        data.last_updated = dt_util.utcnow()

    await data.store.async_save(
        {
            "latitude": latitude,
            "longitude": longitude,
            "updated": (
                data.last_updated.isoformat() if data.last_updated else None
            ),
        }
    )


def _resolve_target_entry(
    hass: HomeAssistant, data: DynamicLocationData
) -> ConfigEntry | None:
    """Resolve the explicitly configured OpenUV entry."""

    target_id = data.openuv_entry_id
    if target_id is None:
        return None
    target = hass.config_entries.async_get_entry(target_id)
    if target is not None and target.domain == OPENUV_DOMAIN:
        return target
    return None


def _get_coordinates(
    entry: ConfigEntry,
) -> tuple[float | None, float | None]:
    """Read latitude and longitude from a target config entry."""

    latitude = entry.data.get(CONF_LATITUDE)
    longitude = entry.data.get(CONF_LONGITUDE)
    try:
        return (
            float(latitude) if latitude is not None else None,
            float(longitude) if longitude is not None else None,
        )
    except (TypeError, ValueError):
        return None, None


def _openuv_location_key(latitude: float, longitude: float) -> str:
    """Return the coordinate key used by OpenUV entities and devices."""

    return f"{latitude}_{longitude}"


def _openuv_config_unique_id(latitude: float, longitude: float) -> str:
    """Return the coordinate unique ID used by the OpenUV config flow."""

    return f"{latitude}, {longitude}"


def _openuv_unique_id_conflicts(
    hass: HomeAssistant, target: ConfigEntry, unique_id: str
) -> bool:
    """Return whether another OpenUV entry already owns a unique ID."""

    return any(
        entry.entry_id != target.entry_id and entry.unique_id == unique_id
        for entry in hass.config_entries.async_entries(OPENUV_DOMAIN)
    )


def _repair_openuv_entry_metadata(
    hass: HomeAssistant,
    target: ConfigEntry,
    latitude: float,
    longitude: float,
) -> None:
    """Align OpenUV config-entry metadata with its stored coordinates."""

    unique_id = _openuv_config_unique_id(latitude, longitude)
    if target.unique_id == unique_id:
        return
    if _openuv_unique_id_conflicts(hass, target, unique_id):
        _LOGGER.warning(
            "Cannot repair OpenUV unique ID to %s because another entry "
            "already uses it",
            unique_id,
        )
        return

    new_title = unique_id if target.title == target.unique_id else target.title
    hass.config_entries.async_update_entry(
        target, unique_id=unique_id, title=new_title
    )


def _migrate_openuv_registry_location(
    hass: HomeAssistant,
    target: ConfigEntry,
    old_latitude: float,
    old_longitude: float,
    new_latitude: float,
    new_longitude: float,
) -> None:
    """Move OpenUV registry IDs to new coordinates before setup."""

    old_key = _openuv_location_key(old_latitude, old_longitude)
    new_key = _openuv_location_key(new_latitude, new_longitude)
    if old_key == new_key:
        return

    entity_registry = er.async_get(hass)
    old_prefix = f"{old_key}_"
    new_prefix = f"{new_key}_"
    for entity in er.async_entries_for_config_entry(
        entity_registry, target.entry_id
    ):
        if entity.platform != OPENUV_DOMAIN:
            continue
        if not entity.unique_id.startswith(old_prefix):
            continue

        new_unique_id = (
            f"{new_prefix}{entity.unique_id.removeprefix(old_prefix)}"
        )
        duplicate_entity_id = entity_registry.async_get_entity_id(
            entity.domain, entity.platform, new_unique_id
        )
        if (
            duplicate_entity_id is not None
            and duplicate_entity_id != entity.entity_id
        ):
            _LOGGER.debug(
                "Removing stale OpenUV entity %s before migrating %s",
                duplicate_entity_id,
                entity.entity_id,
            )
            entity_registry.async_remove(duplicate_entity_id)

        entity_registry.async_update_entity(
            entity.entity_id, new_unique_id=new_unique_id
        )

    device_registry = dr.async_get(hass)
    old_identifier = (OPENUV_DOMAIN, old_key)
    new_identifier = (OPENUV_DOMAIN, new_key)
    old_device = device_registry.async_get_device_by_identifier(
        old_identifier, target.entry_id
    )
    if old_device is None:
        return

    duplicate_device = device_registry.async_get_device_by_identifier(
        new_identifier, target.entry_id
    )
    if duplicate_device is not None and duplicate_device.id != old_device.id:
        _LOGGER.debug(
            "Removing stale OpenUV device %s before migrating %s",
            duplicate_device.id,
            old_device.id,
        )
        device_registry.async_remove_device(duplicate_device.id)

    device_registry.async_update_device(
        old_device.id, new_identifiers={new_identifier}
    )


def _cleanup_stale_openuv_registry_entries(
    hass: HomeAssistant,
    target: ConfigEntry,
    latitude: float,
    longitude: float,
) -> None:
    """Remove OpenUV devices and entities left by earlier location changes."""

    device_registry = dr.async_get(hass)
    current_identifier = (
        OPENUV_DOMAIN,
        _openuv_location_key(latitude, longitude),
    )
    current_device = device_registry.async_get_device_by_identifier(
        current_identifier, target.entry_id
    )
    if current_device is None:
        return

    stale_device_ids = {
        device.id
        for device in dr.async_entries_for_config_entry(
            device_registry, target.entry_id
        )
        if device.id != current_device.id
        and any(
            identifier_domain == OPENUV_DOMAIN
            for identifier_domain, _ in device.identifiers
        )
    }
    if not stale_device_ids:
        return

    entity_registry = er.async_get(hass)
    for entity in er.async_entries_for_config_entry(
        entity_registry, target.entry_id
    ):
        if (
            entity.platform == OPENUV_DOMAIN
            and entity.device_id in stale_device_ids
        ):
            _LOGGER.debug("Removing stale OpenUV entity %s", entity.entity_id)
            entity_registry.async_remove(entity.entity_id)

    for device_id in stale_device_ids:
        _LOGGER.debug("Removing stale OpenUV device %s", device_id)
        device_registry.async_remove_device(device_id)


def _valid_coordinates(latitude: float, longitude: float) -> bool:
    """Validate latitude and longitude."""

    return (
        math.isfinite(latitude)
        and math.isfinite(longitude)
        and -90 <= latitude <= 90
        and -180 <= longitude <= 180
    )


def _distance_km(
    latitude1: float,
    longitude1: float,
    latitude2: float,
    longitude2: float,
) -> float:
    """Return great-circle distance in kilometres."""

    meters = ha_distance(latitude1, longitude1, latitude2, longitude2)
    return 0.0 if meters is None else meters / 1000.0
