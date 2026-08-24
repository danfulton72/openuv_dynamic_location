# OpenUV Dynamic Location

Home Assistant custom integration that keeps a configured **OpenUV** instance aligned with a moving GPS position.

## Features

- Watches configurable latitude and longitude sensor entities.
- Updates OpenUV only after movement exceeds a configurable distance threshold.
- Debounces GPS updates so paired coordinates can settle.
- Supports reconfiguration from Home Assistant without deleting the entry.
- Exposes diagnostic distance and last-successful-update sensors.
- Persists the last applied coordinates across Home Assistant restarts.

## Requirements

- Home Assistant
- OpenUV already configured
- Sensor entities that provide latitude and longitude in decimal degrees

## Install with HACS

1. In HACS, add `https://github.com/danfulton72/openuv_dynamic_location` as a custom repository of type **Integration**.
2. Install **OpenUV Dynamic Location**.
3. Restart Home Assistant.
4. Go to **Settings → Devices & services → Add integration** and search for **OpenUV Dynamic Location**.

## Manual installation

Copy `custom_components/openuv_dynamic_location` from this repository to `config/custom_components/openuv_dynamic_location` and restart Home Assistant.

## Configuration

During setup, choose the latitude sensor, longitude sensor, and the OpenUV config entry to update. The target is required so a multi-instance installation can never update an arbitrary first entry.

Options control the movement threshold (default **25 km**) and debounce delay (default **2 seconds**). Use **Reconfigure** to change GPS sensors or the target integration.

## Diagnostics

- **Distance since last update** reports the most recently measured distance from the last successfully applied location.
- **Last updated** changes only after OpenUV is actually updated and reloads successfully; initializing a reference after startup does not change this timestamp.

## Versioning

The integration manifest is versioned `1.0.2`. HACS displays the latest published GitHub Release; publish a matching `v1.0.2` release for the stable release channel.

## License

MIT — see [LICENSE](LICENSE).
