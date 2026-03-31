import logging

from hermes_utils.parser import Home
from enrichment.profile import get_profiles_with_enrichment
from enrichment.queue import enqueue

logger = logging.getLogger(__name__)


def should_enqueue(home: Home, profile: dict) -> bool:
    """True if home passes price and city pre-filters against the profile."""
    if home.price <= 0:
        logger.debug("Reject %s: invalid price %s", home.address, home.price)
        return False
    if home.price > profile["max_rent"]:
        logger.debug(
            "Reject %s: price %s > max_rent %s",
            home.address, home.price, profile["max_rent"],
        )
        return False
    target_cities = [c.lower() for c in (profile.get("target_cities") or [])]
    if home.city.lower() not in target_cities:
        logger.debug(
            "Reject %s: city %s not in %s", home.address, home.city, target_cities,
        )
        return False
    return True


def enqueue_for_enrichment(new_homes: list[Home]) -> None:
    """Load all profiles, pre-filter each home, enqueue matches.

    Called from scraper.py after broadcast(). Catches all exceptions
    so it never crashes the scraper.
    """
    try:
        profiles = get_profiles_with_enrichment()
        if not profiles:
            return

        enqueued = 0
        for home in new_homes:
            for profile in profiles:
                if should_enqueue(home, profile):
                    if enqueue(home, profile["id"]):
                        enqueued += 1

        logger.warning(
            "Enrichment: enqueued %d of %d new homes for %d profiles",
            enqueued, len(new_homes), len(profiles),
        )
    except Exception as e:
        logger.error("enqueue_for_enrichment failed: %r", e)
