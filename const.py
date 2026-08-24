"""Constants for OpenUV Dynamic Location."""

DOMAIN = "openuv_dynamic_location"

CONF_LATITUDE_ENTITY = "latitude_entity"
CONF_LONGITUDE_ENTITY = "longitude_entity"
CONF_DISTANCE_KM = "distance_km"
CONF_DEBOUNCE_SECONDS = "debounce_seconds"
CONF_OPENUV_ENTRY_ID = "openuv_entry_id"

DEFAULT_LATITUDE_ENTITY = "sensor.rutx50_gps_lat"
DEFAULT_LONGITUDE_ENTITY = "sensor.rutx50_gps_lon"
DEFAULT_DISTANCE_KM = 25.0
DEFAULT_DEBOUNCE_SECONDS = 2.0

OPENUV_DOMAIN = "openuv"

STORAGE_VERSION = 1
STORAGE_KEY = "openuv_dynamic_location"

# Config entry schema version. Bumped from 1 -> 2 when
# CONF_DEBOUNCE_SECONDS and CONF_OPENUV_ENTRY_ID were added.
CONFIG_ENTRY_VERSION = 2

# Dispatcher signal fired whenever a GPS check completes with a valid
# distance reading, so sensor entities can update without polling.
SIGNAL_LOCATION_UPDATED = f"{DOMAIN}_location_updated"
