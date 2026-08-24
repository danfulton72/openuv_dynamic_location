# OpenUV Dynamic Location

Home Assistant custom integration that keeps an OpenUV config entry aligned with a moving GPS position.

It watches configurable latitude and longitude sensor entities, measures movement from the last successfully applied location, and updates the selected OpenUV integration once the configured distance threshold is exceeded.

## Version

Current release: **1.0.0**

## Requirements

- Home Assistant
- The OpenUV integration already configured
- Sensor entities that provide latitude and longitude values

## Installation

1. Create `config/custom_components/openuv_dynamic_location/` in your Home Assistant configuration directory.
2. Copy the integration files from this repository into that directory, keeping the `translations` folder.
3. Restart Home Assistant.
4. Open **Settings → Devices & services → Add integration**.
5. Search for **OpenUV Dynamic Location**.

## Configuration

The setup flow lets you choose the GPS latitude and longitude sensors, the movement threshold in kilometres, a debounce delay, and the OpenUV config entry to update.

Defaults:

- Latitude: `sensor.rutx50_gps_lat`
- Longitude: `sensor.rutx50_gps_lon`
- Movement threshold: **25 km**
- Debounce: **2 seconds**

## Sensors

The integration exposes:

- **Distance since last update** — movement from the last successfully applied OpenUV location.
- **Last updated** — timestamp of the last successful location change.

## How it works

When either GPS entity changes, the integration waits for the debounce period and reads both coordinates. Once movement exceeds the configured threshold, it updates only latitude and longitude on the selected OpenUV config entry and reloads OpenUV. The last successfully applied position is persisted across Home Assistant restarts.

## Multiple OpenUV instances

If you have more than one OpenUV config entry, select the intended target in this integration's options. With no explicit target, the first available OpenUV entry is used.

## License

No license has been specified for this repository.
