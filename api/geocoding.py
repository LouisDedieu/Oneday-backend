"""
api/geocoding.py — Proxy pour les requêtes de géocodage LocationIQ
Protège la clé API en la gardant côté serveur.
"""
import logging
from typing import Optional

import httpx
from fastapi import APIRouter, Query, HTTPException
from pydantic import BaseModel

from config import settings

logger = logging.getLogger("bombo.geocoding")

router = APIRouter(prefix="/geocoding", tags=["geocoding"])


class GeocodingResult(BaseModel):
    lat: float
    lon: float
    display_name: Optional[str] = None


class GeocodingResponse(BaseModel):
    results: list[GeocodingResult]


@router.get("/search", response_model=GeocodingResponse)
async def geocode_search(
    q: str = Query(..., min_length=1, description="Query string to geocode"),
    limit: int = Query(1, ge=1, le=10, description="Maximum number of results"),
):
    """
    Proxy geocoding requests to LocationIQ API.
    Keeps the API key secure on the server side.
    """
    api_key = settings.LOCATIONIQ_API_KEY
    if not api_key:
        logger.error("LOCATIONIQ_API_KEY not configured")
        raise HTTPException(
            status_code=500,
            detail="Geocoding service not configured",
        )

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(
                "https://us1.locationiq.com/v1/search",
                params={
                    "key": api_key,
                    "q": q,
                    "format": "json",
                    "limit": limit,
                },
                headers={"Accept": "application/json"},
            )

            if response.status_code == 429:
                raise HTTPException(
                    status_code=429,
                    detail="Rate limit exceeded. Please try again later.",
                )

            if response.status_code == 404:
                # No results found
                return GeocodingResponse(results=[])

            response.raise_for_status()
            data = response.json()

            if not data or not isinstance(data, list):
                return GeocodingResponse(results=[])

            results = [
                GeocodingResult(
                    lat=float(item["lat"]),
                    lon=float(item["lon"]),
                    display_name=item.get("display_name"),
                )
                for item in data[:limit]
            ]

            return GeocodingResponse(results=results)

    except HTTPException:
        # Re-raise FastAPI exceptions (429, etc.)
        raise
    except httpx.TimeoutException:
        logger.warning("Geocoding request timed out for query: %s", q)
        raise HTTPException(
            status_code=504,
            detail="Geocoding request timed out",
        )
    except httpx.HTTPStatusError as e:
        logger.error("LocationIQ API error: %s", e)
        raise HTTPException(
            status_code=502,
            detail="Geocoding service unavailable",
        )
    except Exception as e:
        logger.error("Unexpected geocoding error: %s", e)
        raise HTTPException(
            status_code=500,
            detail="Internal geocoding error",
        )
