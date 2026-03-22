"""
services/geocoding_service.py — Service de géocodage batch pour les points
Utilise LocationIQ pour convertir les adresses en coordonnées lat/lon.
"""
import logging
import asyncio
from typing import Optional, Tuple, List, Dict, Any

import httpx

from config import settings

logger = logging.getLogger("bombo.services.geocoding")

# Rate limiting: LocationIQ free tier = 2 requests/second
GEOCODING_DELAY = 0.5  # 500ms between requests


async def geocode_query(query: str, timeout: float = 10.0) -> Optional[Tuple[float, float]]:
    """
    Geocode a single query string using LocationIQ API.
    Returns (latitude, longitude) or None if not found.
    """
    api_key = settings.LOCATIONIQ_API_KEY
    if not api_key:
        logger.warning("LOCATIONIQ_API_KEY not configured")
        return None

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.get(
                "https://us1.locationiq.com/v1/search",
                params={
                    "key": api_key,
                    "q": query,
                    "format": "json",
                    "limit": 1,
                },
                headers={"Accept": "application/json"},
            )

            if response.status_code == 429:
                logger.warning("LocationIQ rate limit exceeded")
                return None

            if response.status_code == 404:
                return None

            response.raise_for_status()
            data = response.json()

            if data and isinstance(data, list) and len(data) > 0:
                return (float(data[0]["lat"]), float(data[0]["lon"]))

            return None

    except httpx.TimeoutException:
        logger.warning("Geocoding timeout for query: %s", query)
        return None
    except Exception as e:
        logger.error("Geocoding error for query '%s': %s", query, e)
        return None


async def geocode_highlight(
    name: str,
    address: Optional[str],
    city_name: str,
    country: Optional[str] = None
) -> Optional[Tuple[float, float]]:
    """
    Geocode a city highlight with multiple fallback strategies.
    1. Try address + city + country
    2. Try name + city + country
    3. Try address + city
    4. Try name + city
    """
    city_context = f"{city_name}, {country}" if country else city_name

    # Strategy 1: address + full context
    if address:
        query = f"{address}, {city_context}"
        result = await geocode_query(query)
        if result:
            logger.debug("Geocoded highlight '%s' via address: %s", name, query)
            return result
        await asyncio.sleep(GEOCODING_DELAY)

    # Strategy 2: name + full context
    query = f"{name}, {city_context}"
    result = await geocode_query(query)
    if result:
        logger.debug("Geocoded highlight '%s' via name: %s", name, query)
        return result
    await asyncio.sleep(GEOCODING_DELAY)

    # Strategy 3: address + city only (if country was included before)
    if address and country:
        query = f"{address}, {city_name}"
        result = await geocode_query(query)
        if result:
            logger.debug("Geocoded highlight '%s' via address (no country): %s", name, query)
            return result
        await asyncio.sleep(GEOCODING_DELAY)

    # Strategy 4: name + city only
    if country:
        query = f"{name}, {city_name}"
        result = await geocode_query(query)
        if result:
            logger.debug("Geocoded highlight '%s' via name (no country): %s", name, query)
            return result

    logger.warning("Could not geocode highlight '%s' in %s", name, city_context)
    return None


async def geocode_spot(
    name: str,
    address: Optional[str],
    location: Optional[str],  # Day location (city name)
) -> Optional[Tuple[float, float]]:
    """
    Geocode a trip spot with multiple fallback strategies.
    1. Try address + location
    2. Try name + location
    """
    if not location:
        logger.warning("Cannot geocode spot '%s' without location context", name)
        return None

    # Strategy 1: address + location
    if address:
        query = f"{address}, {location}"
        result = await geocode_query(query)
        if result:
            logger.debug("Geocoded spot '%s' via address: %s", name, query)
            return result
        await asyncio.sleep(GEOCODING_DELAY)

    # Strategy 2: name + location
    query = f"{name}, {location}"
    result = await geocode_query(query)
    if result:
        logger.debug("Geocoded spot '%s' via name: %s", name, query)
        return result

    logger.warning("Could not geocode spot '%s' in %s", name, location)
    return None


async def batch_geocode_highlights(
    highlights: List[Dict[str, Any]],
    city_name: str,
    country: Optional[str] = None,
    update_callback=None,
) -> Dict[str, Tuple[float, float]]:
    """
    Batch geocode multiple highlights.
    Returns a dict mapping highlight_id -> (lat, lon) for successfully geocoded highlights.

    Args:
        highlights: List of highlight dicts with id, name, address, latitude, longitude
        city_name: City name for context
        country: Country name for context
        update_callback: Optional async function(highlight_id, lat, lon) to persist results

    Returns:
        Dict mapping highlight_id to (lat, lon) for successfully geocoded highlights
    """
    results = {}

    # Filter highlights that need geocoding (missing coordinates)
    to_geocode = [
        h for h in highlights
        if h.get("latitude") is None or h.get("longitude") is None
    ]

    if not to_geocode:
        logger.info("All highlights already have coordinates")
        return results

    logger.info("Geocoding %d highlights for %s", len(to_geocode), city_name)

    for highlight in to_geocode:
        highlight_id = highlight["id"]
        name = highlight.get("name", "")
        address = highlight.get("address")

        coords = await geocode_highlight(name, address, city_name, country)

        if coords:
            lat, lon = coords
            results[highlight_id] = coords

            if update_callback:
                try:
                    await update_callback(highlight_id, lat, lon)
                    logger.debug("Persisted coordinates for highlight %s", highlight_id)
                except Exception as e:
                    logger.error("Failed to persist coordinates for highlight %s: %s", highlight_id, e)

        # Rate limiting
        await asyncio.sleep(GEOCODING_DELAY)

    logger.info("Successfully geocoded %d/%d highlights", len(results), len(to_geocode))
    return results


async def geocode_destination(
    location: str,
) -> Optional[Tuple[float, float]]:
    """
    Geocode a trip destination (day location).
    Simply geocodes the location string directly.
    """
    if not location:
        return None

    result = await geocode_query(location)
    if result:
        logger.debug("Geocoded destination '%s'", location)
        return result

    logger.warning("Could not geocode destination '%s'", location)
    return None


async def batch_geocode_destinations(
    destinations: List[Dict[str, Any]],
    update_callback=None,
) -> Dict[str, Tuple[float, float]]:
    """
    Batch geocode trip destinations (itinerary days with locations).
    Returns a dict mapping day_id -> (lat, lon) for successfully geocoded destinations.

    Args:
        destinations: List of day dicts with id, location, latitude, longitude
        update_callback: Optional async function(day_id, lat, lon) to persist results

    Returns:
        Dict mapping day_id to (lat, lon) for successfully geocoded destinations
    """
    results = {}

    # Filter destinations that need geocoding (missing coordinates)
    to_geocode = [
        d for d in destinations
        if d.get("location") and (d.get("latitude") is None or d.get("longitude") is None)
    ]

    if not to_geocode:
        logger.info("All destinations already have coordinates")
        return results

    logger.info("Geocoding %d destinations", len(to_geocode))

    for dest in to_geocode:
        dest_id = dest["id"]
        location = dest.get("location", "")

        coords = await geocode_destination(location)

        if coords:
            lat, lon = coords
            results[dest_id] = coords

            if update_callback:
                try:
                    await update_callback(dest_id, lat, lon)
                    logger.debug("Persisted coordinates for destination %s", dest_id)
                except Exception as e:
                    logger.error("Failed to persist coordinates for destination %s: %s", dest_id, e)

        # Rate limiting
        await asyncio.sleep(GEOCODING_DELAY)

    logger.info("Successfully geocoded %d/%d destinations", len(results), len(to_geocode))
    return results


async def batch_geocode_spots(
    spots: List[Dict[str, Any]],
    location: Optional[str],
    update_callback=None,
) -> Dict[str, Tuple[float, float]]:
    """
    Batch geocode multiple spots.
    Returns a dict mapping spot_id -> (lat, lon) for successfully geocoded spots.

    Args:
        spots: List of spot dicts with id, name, address, latitude, longitude
        location: Location context (city name from day)
        update_callback: Optional async function(spot_id, lat, lon) to persist results

    Returns:
        Dict mapping spot_id to (lat, lon) for successfully geocoded spots
    """
    results = {}

    if not location:
        logger.warning("No location context provided for batch spot geocoding")
        return results

    # Filter spots that need geocoding (missing coordinates)
    to_geocode = [
        s for s in spots
        if s.get("latitude") is None or s.get("longitude") is None
    ]

    if not to_geocode:
        logger.info("All spots already have coordinates")
        return results

    logger.info("Geocoding %d spots for %s", len(to_geocode), location)

    for spot in to_geocode:
        spot_id = spot["id"]
        name = spot.get("name", "")
        address = spot.get("address")

        coords = await geocode_spot(name, address, location)

        if coords:
            lat, lon = coords
            results[spot_id] = coords

            if update_callback:
                try:
                    await update_callback(spot_id, lat, lon)
                    logger.debug("Persisted coordinates for spot %s", spot_id)
                except Exception as e:
                    logger.error("Failed to persist coordinates for spot %s: %s", spot_id, e)

        # Rate limiting
        await asyncio.sleep(GEOCODING_DELAY)

    logger.info("Successfully geocoded %d/%d spots", len(results), len(to_geocode))
    return results
