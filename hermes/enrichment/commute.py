import logging
import os

import requests

logger = logging.getLogger(__name__)

GOOGLE_MAPS_API_KEY = os.getenv("GOOGLE_MAPS_API_KEY", "")
_DISTANCE_MATRIX_URL = "https://maps.googleapis.com/maps/api/distancematrix/json"


def get_commute_times(origin: str, destination: str) -> dict[str, str] | None:
    """
    Return commute durations from origin → destination for driving, transit, bicycling.
    Returns None if the API key is not configured or the request fails.
    """
    if not GOOGLE_MAPS_API_KEY:
        return None

    results: dict[str, str] = {}
    for mode in ("driving", "transit", "bicycling"):
        try:
            resp = requests.get(
                _DISTANCE_MATRIX_URL,
                params={
                    "origins": origin,
                    "destinations": destination,
                    "mode": mode,
                    "key": GOOGLE_MAPS_API_KEY,
                    "language": "en",
                },
                timeout=5,
            )
            data = resp.json()
            element = (data.get("rows") or [{}])[0].get("elements", [{}])[0]
            if element.get("status") == "OK":
                results[mode] = element["duration"]["text"]
        except Exception as e:
            logger.warning("Google Maps commute lookup failed (mode=%s): %r", mode, e)

    return results or None


def format_commute_times(times: dict[str, str]) -> str:
    """Format commute times dict into a readable inline string."""
    icons = {"driving": "🚗", "transit": "🚌", "bicycling": "🚲"}
    parts = [f"{icons[m]} {t}" for m, t in times.items() if m in icons]
    return "  ·  ".join(parts)
