"""Known ADS-B feeder units and web UIs.

This is a community catalog — not a specific site. On connect we probe the
server and only surface what is actually installed / responding.
"""

from __future__ import annotations

# systemd unit → display name (order is the preferred UI order)
KNOWN_SERVICES: list[tuple[str, str]] = [
    ("dump1090-fa", "dump1090-fa"),
    ("readsb", "readsb"),
    ("dump1090", "dump1090"),
    ("dump978-fa", "dump978-fa"),
    ("tar1090", "tar1090"),
    ("graphs1090", "graphs1090"),
    ("piaware", "PiAware"),
    ("fr24feed", "FlightRadar24"),
    ("adsbexchange-feed", "ADS-B Exchange"),
    ("adsbexchange-mlat", "ADS-B Exchange MLAT"),
    ("airplanes-feed", "airplanes.live"),
    ("airplanes-mlat", "airplanes.live MLAT"),
    ("rbfeeder", "RadarBox"),
    ("pfclient", "Plane Finder"),
    ("opensky-feeder", "OpenSky"),
    ("adsbfi-feed", "adsb.fi"),
    ("adsbfi-mlat", "adsb.fi MLAT"),
    ("adsbhub", "ADSBHub"),
    ("beast-splitter", "beast-splitter"),
]

UNIT_LABELS: dict[str, str] = dict(KNOWN_SERVICES)

# Decoder stats live next to aircraft.json
JSON_DIRS = (
    "/run/dump1090-fa",
    "/run/readsb",
    "/run/dump1090",
    "/run/dump978-fa",
)

# Probe on the feeder (localhost). `public` is what we open in the browser.
# `units` hints which service row should select this map.
MAP_PROBES: list[dict[str, object]] = [
    {
        "name": "Local map (:8080)",
        "probe": "http://127.0.0.1:8080/",
        "public": "http://{host}:8080/",
        "units": ("tar1090", "dump1090-fa", "piaware", "readsb"),
    },
    {
        "name": "tar1090",
        "probe": "http://127.0.0.1/tar1090/",
        "public": "http://{host}/tar1090/",
        "units": ("tar1090",),
    },
    {
        "name": "PiAware SkyAware",
        "probe": "http://127.0.0.1/skyaware/",
        "public": "http://{host}/skyaware/",
        "units": ("piaware", "dump1090-fa"),
    },
    {
        "name": "dump1090-fa",
        "probe": "http://127.0.0.1/dump1090-fa/",
        "public": "http://{host}/dump1090-fa/",
        "units": ("dump1090-fa",),
    },
    {
        "name": "ADS-B Exchange",
        "probe": "http://127.0.0.1/adsbx/",
        "public": "http://{host}/adsbx/",
        "units": ("adsbexchange-feed",),
    },
    {
        "name": "graphs1090",
        "probe": "http://127.0.0.1/graphs1090/",
        "public": "http://{host}/graphs1090/",
        "units": ("graphs1090",),
    },
    {
        "name": "graphs1090 (:8080)",
        "probe": "http://127.0.0.1:8080/graphs1090/",
        "public": "http://{host}:8080/graphs1090/",
        "units": ("graphs1090",),
    },
    {
        "name": "FlightRadar24",
        "probe": "http://127.0.0.1:8754/",
        "public": "http://{host}:8754/",
        "units": ("fr24feed",),
    },
    {
        "name": "Plane Finder",
        "probe": "http://127.0.0.1:30053/",
        "public": "http://{host}:30053/",
        "units": ("pfclient",),
    },
]

# Fallback public URL if HTTP probe isn't possible (no curl)
UNIT_TO_MAP: dict[str, str] = {
    "piaware": "PiAware SkyAware",
    "dump1090-fa": "Local map (:8080)",
    "readsb": "Local map (:8080)",
    "tar1090": "tar1090",
    "graphs1090": "graphs1090",
    "adsbexchange-feed": "ADS-B Exchange",
    "fr24feed": "FlightRadar24",
    "pfclient": "Plane Finder",
}

ADSB_PACKAGES = [
    "dump1090-fa",
    "piaware",
    "fr24feed",
    "tar1090",
    "graphs1090",
    "readsb",
    "dump978-fa",
]

WEB_PORTS = (80, 8080, 8754, 30003, 30005, 30053, 31003, 10001)

OK_HTTP = {200, 301, 302, 303, 304, 401, 403}
