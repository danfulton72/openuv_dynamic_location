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
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import (
    async_call_later,
    async_track_state_change_event,
)
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util
from homeassistant.util.location import distance as ha_distance

from .const import (
    CONFIG_ENTRY_VERSION,
    CONF_DEBOUNCE_SECONDS,
    CONF_DISTANCE_KM,
    CONF_LATITUDE_ENTITY,
    CONF_LONGITUDE_ENTITY,
    CONF_OPENUV_ENTRY_ID,
    DEFAULT_DEBOUNCE_SECONDS,
    DEFAULT_DISTANCE_KM,
    DEFAULT_LATITUDE_ENTITY,
    DEFAULT_LONGITUDE_ENTITY,
    DOMAIN,
    OPENUV_DOMAIN,
    SIGNAL_LOCATION_UPDATED,
    STORAGE_KEY,
    STORAGE_VERSION,
)

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.SENSOR]


@dataclass
class DynamicLocationData:
    """Runtime state for a single config entry."""

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


def _option(entry: ConfigEntry, key: str, default: Any = None) -> Any:
    """Read a value from options, falling back to data, then a default.

    Options are set by the options flow and always take priority so a
    user can change settings after setup without deleting the entry.
    """

    if key in entry.options:
        return entry.options[key]

    return entry.data.get(key, default)


async def async_setup(
    hass: HomeAssistant,
    config: dict[str, Any],
) -> bool:
    """Set up OpenUV Dynamic Location."""

    return True


async def async_migrate_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
) -> bool:
    """Migrate an old config entry to the current version.

    Version 1 entries predate the debounce and OpenUV target options;
    no data needs to move since settings are read through
    options-then-data, so this only advances the stored version.
    """

    if entry.version < CONFIG_ENTRY_VERSION:
        _LOGGER.debug(
            "Migrating OpenUV Dynamic Location entry from "
            "version %s to %s",
            entry.version,
            CONFIG_ENTRY_VERSION,
        )
        hass.config_entries.async_update_entry(
            entry,
            version=CONFIG_ENTRY_VERSION,
        )

    return True


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
) -> bool:
    """Set up OpenUV Dynamic Location."""

    store = Store[dict[str, float | None]](
        hass,
        STORAGE_VERSION,
        f"{STORAGE_KEY}_{entry.entry_id}",
    )

    stored = await store.async_load() or {}

    stored_updated = stored.get("updated")

    data = DynamicLocationData(
        store=store,
        last_latitude=stored.get("latitude"),
        last_longitude=stored.get("longitude"),
        last_updated=(
            dt_util.parse_datetime(stored_updated)
            if stored_updated is not None
            else None
        ),
        latitude_entity=_option(
            entry, CONF_LATITUDE_ENTITY, DEFAULT_LATITUDE_ENTITY
        ),
        longitude_entity=_option(
            entry, CONF_LONGITUDE_ENTITY, DEFAULT_LONGITUDE_ENTITY
        ),
        distance_km=float(
            _option(entry, CONF_DISTANCE_KM, DEFAULT_DISTANCE_KM)
        ),
        debounce_seconds=float(
            _option(
                entry, CONF_DEBOUNCE_SECONDS, DEFAULT_DEBOUNCE_SECONDS
            )
        ),
        openuv_entry_id=_option(entry, CONF_OPENUV_ENTRY_ID, None),
    )

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = data

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

    # Reload whenever the options flow saves new settings, so the new
    # entities/threshold/target take effect immediately.
    entry.async_on_unload(
        entry.add_update_listener(_async_options_updated)
    )

    # Perform an initial check. Missing GPS entities are normal
    # during startup because the GPS source integration may
    # initialize after this integration.
    _schedule_location_check(hass, entry, data, delay=0)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    return True


async def async_unload_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
) -> bool:
    """Unload OpenUV Dynamic Location."""

    unload_ok = await hass.config_entries.async_unload_platforms(
        entry, PLATFORMS
    )

    if not unload_ok:
        return False

    data: DynamicLocationData | None = hass.data.get(DOMAIN, {}).pop(
        entry.entry_id, None
    )

    if data is not None and data.debounce_cancel is not None:
        data.debounce_cancel()
        data.debounce_cancel = None

    return True


async def _async_options_updated(
    hass: HomeAssistant,
    entry: ConfigEntry,
) -> None:
    """Reload the entry after its options change."""

    await hass.config_entries.async_reload(entry.entry_id)


@callback
def _schedule_location_check(
    hass: HomeAssistant,
    entry: ConfigEntry,
    data: DynamicLocationData,
    delay: float | None = None,
) -> None:
    """Schedule a debounced GPS location check."""

    if data.debounce_cancel is not None:
        data.debounce_cancel()

    data.debounce_cancel = async_call_later(
        hass,
        data.debounce_seconds if delay is None else delay,
        lambda _: hass.create_task(
            _async_check_location(hass, entry, data)
        ),
    )


async def _async_check_location(
    hass: HomeAssistant,
    entry: ConfigEntry,
    data: DynamicLocationData,
) -> None:
    """Check GPS position and update OpenUV if required."""

    data.debounce_cancel = None

    async with data.update_lock:
        latitude_state = hass.states.get(data.latitude_entity)
        longitude_state = hass.states.get(data.longitude_entity)

        # The GPS source integration may not have created its
        # entities yet during Home Assistant startup.
        #
        # This is deliberately not logged as an error.
        if latitude_state is None:
            _LOGGER.debug(
                "GPS latitude sensor is not available yet: %s",
                data.latitude_entity,
            )
            return

        if longitude_state is None:
            _LOGGER.debug(
                "GPS longitude sensor is not available yet: %s",
                data.longitude_entity,
            )
            return

        if latitude_state.state in ("unknown", "unavailable"):
            _LOGGER.debug(
                "GPS latitude sensor is currently %s",
                latitude_state.state,
            )
            return

        if longitude_state.state in ("unknown", "unavailable"):
            _LOGGER.debug(
                "GPS longitude sensor is currently %s",
                longitude_state.state,
            )
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
                "GPS coordinates outside valid range: "
                "latitude=%.6f longitude=%.6f",
                latitude,
                longitude,
            )
            return

        openuv_entry = _resolve_openuv_entry(hass, data)

        if openuv_entry is None:
            _LOGGER.error(
                "No OpenUV config entry was found. Choose one under "
                "this integration's options if you have more than "
                "one OpenUV instance configured."
            )
            return

        reference_latitude = data.last_latitude
        reference_longitude = data.last_longitude

        # First run:
        #
        # Establish the movement reference from the coordinates
        # currently configured in OpenUV.
        #
        # This prevents a restart from immediately treating the
        # current GPS position as a large movement.
        if reference_latitude is None or reference_longitude is None:
            (
                reference_latitude,
                reference_longitude,
            ) = _get_coordinates(openuv_entry)

            if reference_latitude is None or reference_longitude is None:
                _LOGGER.warning(
                    "OpenUV does not have valid configured "
                    "coordinates; using current GPS position as "
                    "the initial reference"
                )

                await _save_reference(data, latitude, longitude)

                return

            await _save_reference(
                data, reference_latitude, reference_longitude
            )

        distance_km = _distance_km(
            reference_latitude, reference_longitude, latitude, longitude
        )
        data.last_distance_km = distance_km

        async_dispatcher_send(
            hass,
            f"{SIGNAL_LOCATION_UPDATED}_{entry.entry_id}",
        )

        if distance_km <= data.distance_km:
            _LOGGER.debug(
                "GPS position is %.2f km from the last "
                "OpenUV location; threshold is %.2f km",
                distance_km,
                data.distance_km,
            )
            return

        current_latitude, current_longitude = _get_coordinates(
            openuv_entry
        )

        # OpenUV is already configured for the exact GPS
        # coordinates. Synchronize our persistent reference.
        if current_latitude == latitude and current_longitude == longitude:
            await _save_reference(data, latitude, longitude)
            return

        new_data = dict(openuv_entry.data)

        # These are the standard Home Assistant config-entry keys
        # OpenUV's own integration uses for its stored coordinates.
        new_data[CONF_LATITUDE] = latitude
        new_data[CONF_LONGITUDE] = longitude

        _LOGGER.info(
            "Updating OpenUV location after %.2f km: "
            "(%.6f, %.6f) -> (%.6f, %.6f)",
            distance_km,
            current_latitude
            if current_latitude is not None
            else reference_latitude,
            current_longitude
            if current_longitude is not None
            else reference_longitude,
            latitude,
            longitude,
        )

        # Preserve all existing OpenUV configuration - the API key,
        # elevation, and protection-window options. Only latitude
        # and longitude are changed.
        hass.config_entries.async_update_entry(
            openuv_entry,
            data=new_data,
        )

        try:
            reload_success = await hass.config_entries.async_reload(
                openuv_entry.entry_id
            )
        except Exception:
            _LOGGER.exception(
                "Exception while reloading OpenUV after "
                "changing its location"
            )
            return

        if not reload_success:
            _LOGGER.error(
                "OpenUV reload failed after changing location. The "
                "movement reference was not advanced, so a later "
                "qualifying GPS update can retry."
            )
            return

        # OpenUV's async_setup_entry() performs its own initial
        # coordinator refresh using the new coordinates, so no
        # update_entity call is required here.
        await _save_reference(data, latitude, longitude)

        async_dispatcher_send(
            hass,
            f"{SIGNAL_LOCATION_UPDATED}_{entry.entry_id}",
        )

        _LOGGER.info(
            "OpenUV location successfully updated to %.6f, %.6f",
            latitude,
            longitude,
        )


async def _save_reference(
    data: DynamicLocationData,
    latitude: float,
    longitude: float,
) -> None:
    """Persist the last successfully applied location."""

    data.last_latitude = latitude
    data.last_longitude = longitude
    data.last_distance_km = 0.0
    data.last_updated = dt_util.utcnow()

    await data.store.async_save(
        {
            "latitude": latitude,
            "longitude": longitude,
            "updated": data.last_updated.isoformat(),
        }
    )


def _resolve_openuv_entry(
    hass: HomeAssistant,
    data: DynamicLocationData,
) -> ConfigEntry | None:
    """Find the OpenUV config entry to update.

    If the user selected a specific entry in the options flow, that
    exact entry is used - this is required to support setups with
    more than one OpenUV instance (e.g. one per vehicle). Otherwise
    this falls back to the first OpenUV entry found, for simple
    single-instance setups.
    """

    if data.openuv_entry_id is not None:
        entry = hass.config_entries.async_get_entry(
            data.openuv_entry_id
        )

        if entry is not None and entry.domain == OPENUV_DOMAIN:
            return entry

        _LOGGER.warning(
            "Configured OpenUV target (%s) no longer exists; "
            "falling back to the first OpenUV entry found. Update "
            "this integration's options to fix this.",
            data.openuv_entry_id,
        )

    entries = hass.config_entries.async_entries(OPENUV_DOMAIN)

    if not entries:
        return None

    if len(entries) > 1:
        _LOGGER.warning(
            "Multiple OpenUV config entries found and none is "
            "selected in this integration's options; using the "
            "first one (%s)",
            entries[0].title,
        )

    return entries[0]


def _get_coordinates(
    entry: ConfigEntry,
) -> tuple[float | None, float | None]:
    """Get coordinates from an OpenUV config entry."""

    latitude = entry.data.get(CONF_LATITUDE)
    longitude = entry.data.get(CONF_LONGITUDE)

    try:
        return (
            float(latitude) if latitude is not None else None,
            float(longitude) if longitude is not None else None,
        )
    except (TypeError, ValueError):
        return None, None


def _valid_coordinates(latitude: float, longitude: float) -> bool:
    """Validate latitude and longitude."""

    return (
        math.isfinite(latitude)
        and math.isfinite(longitude)
        and -90.0 <= latitude <= 90.0
        and -180.0 <= longitude <= 180.0
    )


def _distance_km(
    latitude1: float,
    longitude1: float,
    latitude2: float,
    longitude2: float,
) -> float:
    """Calculate great-circle distance in kilometres.

    Uses Home Assistant's own distance utility (the same one that
    backs the `distance()` template function) instead of a bespoke
    haversine implementation, so this stays consistent with the rest
    of Home Assistant if that utility's model ever changes.
    """

    meters = ha_distance(latitude1, longitude1, latitude2, longitude2)

    if meters is None:
        return 0.0

    return meters / 1000.0
