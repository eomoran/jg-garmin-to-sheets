# GarminGo Agent Notes

GarminGo is an unofficial, read-only Garmin Connect integration. The local MCP server must not write to Garmin, upload activities, edit activities, or mutate Garmin account/device state.

## Source References

Do not maintain a static list of supported Garmin endpoints in this file. Endpoint availability depends on the installed `garminconnect` package and may change after dependency updates.

- Official Garmin Connect Developer Program: https://developer.garmin.com/gc-developer-program/
- Official Garmin Activity API: https://developer.garmin.com/gc-developer-program/activity-api/
- Official Garmin Health API: https://developer.garmin.com/gc-developer-program/health-api/
- Official Garmin Training API: https://developer.garmin.com/gc-developer-program/training-api/
- Unofficial Python client used by this repo: https://github.com/cyberjunky/python-garminconnect

For current MCP endpoint coverage, inspect `garmin://garmin-endpoints` or call `list_garmin_endpoints`. The generic bridge only exposes `garminconnect.Garmin.get_*` methods.

## History

Prefer SQLite history/cache for repeated analysis. The default database path is `output/garmingo.sqlite3`, overrideable with `GARMINGO_DB_PATH` or `USER<N>_SQLITE_PATH`.

Use `refresh=true` only when live Garmin data is needed. Same-day Garmin values may change throughout the day.
