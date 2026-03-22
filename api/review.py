"""
api/review.py — Endpoints du mode review (validation d'itinéraire)
"""
import logging
import asyncio
from typing import List, Optional, Dict, Any
from fastapi import APIRouter, HTTPException, Depends, BackgroundTasks
from pydantic import BaseModel

from utils.auth import get_current_user_id
from services.supabase_service import SupabaseService
from services.geocoding_service import batch_geocode_spots, batch_geocode_destinations
from models.errors import ErrorCode, get_error_message

logger = logging.getLogger("bombo.api.review")
router = APIRouter(prefix="/review", tags=["review"])

_supabase_service: Optional[SupabaseService] = None


def set_supabase_service(service: SupabaseService):
    global _supabase_service
    _supabase_service = service


def _require_supabase():
    if not _supabase_service or not _supabase_service.is_configured():
        raise HTTPException(503, detail="Supabase non configuré")
    return _supabase_service.supabase_client


def _check_day_ownership(sb, day_id: str, user_id: str) -> None:
    """
    Vérifie que le jour existe et appartient à un trip de l'utilisateur.
    Utilise 2 requêtes séparées pour éviter les problèmes de jointure Supabase.
    """
    try:
        # 1. Récupérer le jour et son trip_id
        day_res = sb.from_("itinerary_days") \
            .select("id, trip_id") \
            .eq("id", day_id) \
            .maybe_single() \
            .execute()

        if not day_res or not day_res.data:
            raise HTTPException(404, detail={
                "error_code": ErrorCode.DAY_NOT_FOUND,
                "message": get_error_message(ErrorCode.DAY_NOT_FOUND),
            })

        trip_id = day_res.data.get("trip_id")
        if not trip_id:
            raise HTTPException(404, detail={
                "error_code": ErrorCode.DAY_NOT_FOUND,
                "message": get_error_message(ErrorCode.DAY_NOT_FOUND),
            })

        # 2. Vérifier que le trip appartient à l'utilisateur
        trip_res = sb.from_("trips") \
            .select("id") \
            .eq("id", trip_id) \
            .eq("user_id", user_id) \
            .maybe_single() \
            .execute()

        if not trip_res or not trip_res.data:
            raise HTTPException(404, detail={
                "error_code": ErrorCode.DAY_NOT_FOUND,
                "message": get_error_message(ErrorCode.DAY_NOT_FOUND),
            })

    except HTTPException:
        raise
    except Exception:
        raise HTTPException(404, detail={
            "error_code": ErrorCode.DAY_NOT_FOUND,
            "message": get_error_message(ErrorCode.DAY_NOT_FOUND),
        })


def _check_spot_ownership(sb, spot_id: str, user_id: str) -> None:
    """
    Vérifie que le spot existe et appartient à un trip de l'utilisateur.
    Utilise 3 requêtes séparées pour éviter les problèmes de jointure Supabase.
    """
    try:
        # 1. Récupérer le spot et son itinerary_day_id
        spot_res = sb.from_("spots") \
            .select("id, itinerary_day_id") \
            .eq("id", spot_id) \
            .maybe_single() \
            .execute()

        if not spot_res or not spot_res.data:
            raise HTTPException(404, detail={
                "error_code": ErrorCode.SPOT_NOT_FOUND,
                "message": get_error_message(ErrorCode.SPOT_NOT_FOUND),
            })

        day_id = spot_res.data.get("itinerary_day_id")
        if not day_id:
            raise HTTPException(404, detail={
                "error_code": ErrorCode.SPOT_NOT_FOUND,
                "message": get_error_message(ErrorCode.SPOT_NOT_FOUND),
            })

        # 2. Récupérer le jour et son trip_id
        day_res = sb.from_("itinerary_days") \
            .select("id, trip_id") \
            .eq("id", day_id) \
            .maybe_single() \
            .execute()

        if not day_res or not day_res.data:
            raise HTTPException(404, detail={
                "error_code": ErrorCode.SPOT_NOT_FOUND,
                "message": get_error_message(ErrorCode.SPOT_NOT_FOUND),
            })

        trip_id = day_res.data.get("trip_id")
        if not trip_id:
            raise HTTPException(404, detail={
                "error_code": ErrorCode.SPOT_NOT_FOUND,
                "message": get_error_message(ErrorCode.SPOT_NOT_FOUND),
            })

        # 3. Vérifier que le trip appartient à l'utilisateur
        trip_res = sb.from_("trips") \
            .select("id") \
            .eq("id", trip_id) \
            .eq("user_id", user_id) \
            .maybe_single() \
            .execute()

        if not trip_res or not trip_res.data:
            raise HTTPException(404, detail={
                "error_code": ErrorCode.SPOT_NOT_FOUND,
                "message": get_error_message(ErrorCode.SPOT_NOT_FOUND),
            })

    except HTTPException:
        raise
    except Exception:
        raise HTTPException(404, detail={
            "error_code": ErrorCode.SPOT_NOT_FOUND,
            "message": get_error_message(ErrorCode.SPOT_NOT_FOUND),
        })


def _check_destination_ownership(sb, dest_id: str, user_id: str) -> None:
    """
    Vérifie que la destination existe et appartient à un trip de l'utilisateur.
    Utilise 2 requêtes séparées pour éviter les problèmes de jointure Supabase.
    """
    try:
        # 1. Récupérer la destination et son trip_id
        dest_res = sb.from_("destinations") \
            .select("id, trip_id") \
            .eq("id", dest_id) \
            .maybe_single() \
            .execute()

        if not dest_res or not dest_res.data:
            raise HTTPException(404, detail={
                "error_code": ErrorCode.DESTINATION_NOT_FOUND,
                "message": get_error_message(ErrorCode.DESTINATION_NOT_FOUND),
            })

        trip_id = dest_res.data.get("trip_id")
        if not trip_id:
            raise HTTPException(404, detail={
                "error_code": ErrorCode.DESTINATION_NOT_FOUND,
                "message": get_error_message(ErrorCode.DESTINATION_NOT_FOUND),
            })

        # 2. Vérifier que le trip appartient à l'utilisateur
        trip_res = sb.from_("trips") \
            .select("id") \
            .eq("id", trip_id) \
            .eq("user_id", user_id) \
            .maybe_single() \
            .execute()

        if not trip_res or not trip_res.data:
            raise HTTPException(404, detail={
                "error_code": ErrorCode.DESTINATION_NOT_FOUND,
                "message": get_error_message(ErrorCode.DESTINATION_NOT_FOUND),
            })

    except HTTPException:
        raise
    except Exception:
        raise HTTPException(404, detail={
            "error_code": ErrorCode.DESTINATION_NOT_FOUND,
            "message": get_error_message(ErrorCode.DESTINATION_NOT_FOUND),
        })


# ── Modèles ───────────────────────────────────────────────────────────────────

class ValidateDayBody(BaseModel):
    validated: bool


class SpotUpdateBody(BaseModel):
    name: Optional[str] = None
    spot_type: Optional[str] = None
    address: Optional[str] = None
    duration_minutes: Optional[int] = None
    price_range: Optional[str] = None
    tips: Optional[str] = None
    highlight: Optional[bool] = None


class CoordinatesBody(BaseModel):
    lat: float
    lon: float


class AddCityToTripBody(BaseModel):
    city_id: str
    day_id: Optional[str] = None  # If provided, add highlights to this day
    create_new_day: bool = False  # If true and day_id is None, create a new day


class AddDestinationBody(BaseModel):
    city_name: str
    country: Optional[str] = None
    latitude: float
    longitude: float


class DestinationOrderItem(BaseModel):
    id: str
    order: int


class ReorderDestinationsBody(BaseModel):
    destinations: List[DestinationOrderItem]


# ── Routes ────────────────────────────────────────────────────────────────────

@router.get("/{trip_id}")
async def get_trip_for_review(trip_id: str) -> Dict:
    """
    Retourne le trip complet pour le mode review :
    trip + première destination + jours + spots (triés par spot_order).
    """
    sb = _require_supabase()

    def fetch_trip():
        return sb.from_("trips") \
            .select("id, trip_title, vibe, duration_days, source_url, content_creator_handle") \
            .eq("id", trip_id) \
            .single() \
            .execute()

    def fetch_destinations():
        return sb.from_("destinations") \
            .select("id, city, country, visit_order") \
            .eq("trip_id", trip_id) \
            .order("visit_order") \
            .execute()

    def fetch_days():
        return sb.from_("itinerary_days") \
            .select("*") \
            .eq("trip_id", trip_id) \
            .order("day_number") \
            .execute()

    trip_res, dests_res, days_res = await asyncio.gather(
        asyncio.to_thread(fetch_trip),
        asyncio.to_thread(fetch_destinations),
        asyncio.to_thread(fetch_days),
    )

    if not trip_res.data:
        raise HTTPException(404, detail="Trip introuvable")

    trip = trip_res.data
    dests = dests_res.data or []
    days = days_res.data or []

    # Destination principale (première par visit_order)
    first_dest = dests[0] if dests else None
    destination_str = ", ".join(
        filter(None, [first_dest.get("city"), first_dest.get("country")])
    ) if first_dest else "Destination inconnue"

    # Spots de tous les jours
    day_ids = [d["id"] for d in days]
    spots = []
    if day_ids:
        spots_res = await asyncio.to_thread(
            lambda: sb.from_("spots").select("*").in_("itinerary_day_id", day_ids).execute()
        )
        spots = spots_res.data or []

    # Grouper les spots par jour
    spots_by_day: Dict[str, List] = {}
    for s in spots:
        day_id = s["itinerary_day_id"]
        spots_by_day.setdefault(day_id, []).append(s)

    db_days = []
    for d in days:
        day_spots = sorted(
            spots_by_day.get(d["id"], []),
            key=lambda s: s.get("spot_order") or 0,
        )
        db_days.append({
            "id":                 d["id"],
            "day_number":         d["day_number"],
            "location":           d.get("location"),
            "destination_id":     d.get("destination_id"),
            "theme":              d.get("theme"),
            "accommodation_name": d.get("accommodation_name"),
            "breakfast_spot":     d.get("breakfast_spot"),
            "lunch_spot":         d.get("lunch_spot"),
            "dinner_spot":        d.get("dinner_spot"),
            "validated":          d.get("validated", True),
            "spots": [
                {
                    "id":               s["id"],
                    "name":             s["name"],
                    "spot_type":        s.get("spot_type"),
                    "address":          s.get("address"),
                    "duration_minutes": s.get("duration_minutes"),
                    "price_range":      s.get("price_range"),
                    "tips":             s.get("tips"),
                    "highlight":        s.get("highlight", False),
                    "latitude":         s.get("latitude"),
                    "longitude":        s.get("longitude"),
                }
                for s in day_spots
            ],
        })

    return {
        **trip,
        "destination": destination_str,
        "days": db_days,
    }


@router.patch("/days/{day_id}/validate", status_code=200)
async def validate_day(
    day_id: str,
    body: ValidateDayBody,
    user_id: str = Depends(get_current_user_id),
) -> Dict:
    """Met à jour le flag validated d'un jour d'itinéraire."""
    sb = _require_supabase()
    _check_day_ownership(sb, day_id, user_id)
    res = sb.from_("itinerary_days") \
        .update({"validated": body.validated}) \
        .eq("id", day_id) \
        .execute()
    return {"updated": True}


async def _geocode_review_in_background(trip_id: str) -> None:
    """
    Background task to geocode all spots and destinations for a trip.
    This runs AFTER the sync is complete, so it doesn't block the UI.
    """
    if not _supabase_service or not _supabase_service.is_configured():
        logger.warning("Supabase not configured for background geocoding")
        return

    sb = _supabase_service.supabase_client
    geocoded_spots = 0
    geocoded_destinations = 0

    try:
        # Récupérer les jours validés avec leur location et coordonnées
        days_res = sb.from_("itinerary_days") \
            .select("id, location, latitude, longitude") \
            .eq("trip_id", trip_id) \
            .eq("validated", True) \
            .execute()
        valid_days = days_res.data or []

        if not valid_days:
            logger.info(f"No valid days found for trip {trip_id}, skipping geocoding")
            return

        valid_day_ids = [d["id"] for d in valid_days]
        day_location_map = {d["id"]: d.get("location") for d in valid_days}

        # 1. Geocoder les destinations (jours) sans coordonnées
        async def update_day_coords(day_id: str, lat: float, lon: float):
            await asyncio.to_thread(
                lambda: sb.from_("itinerary_days")
                    .update({"latitude": lat, "longitude": lon})
                    .eq("id", day_id)
                    .execute()
            )

        dest_results = await batch_geocode_destinations(
            destinations=valid_days,
            update_callback=update_day_coords,
        )
        geocoded_destinations = len(dest_results)

        # 2. Récupérer et geocoder les spots sans coordonnées
        spots_res = sb.from_("spots") \
            .select("id, name, address, latitude, longitude, itinerary_day_id") \
            .in_("itinerary_day_id", valid_day_ids) \
            .execute()
        all_spots = spots_res.data or []

        async def update_spot_coords(spot_id: str, lat: float, lon: float):
            await asyncio.to_thread(
                lambda: sb.from_("spots")
                    .update({"latitude": lat, "longitude": lon})
                    .eq("id", spot_id)
                    .execute()
            )

        # Grouper les spots par location et geocoder
        spots_by_location: Dict[str, List] = {}
        for spot in all_spots:
            day_id = spot.get("itinerary_day_id")
            location = day_location_map.get(day_id)
            if location:
                spots_by_location.setdefault(location, []).append(spot)

        for location, spots in spots_by_location.items():
            results = await batch_geocode_spots(
                spots=spots,
                location=location,
                update_callback=update_spot_coords,
            )
            geocoded_spots += len(results)

        logger.info(
            f"Background geocoding complete for trip {trip_id}: "
            f"{geocoded_destinations} destinations, {geocoded_spots} spots"
        )

    except Exception as e:
        logger.error(f"Background geocoding failed for trip {trip_id}: {e}")


@router.post("/{trip_id}/sync", status_code=200)
async def sync_destinations(
    trip_id: str,
    background_tasks: BackgroundTasks,
    user_id: str = Depends(get_current_user_id),
) -> Dict:
    """
    Synchronise les destinations après validation des jours :
    1. Supprime spots des jours non-validés
    2. Supprime jours non-validés
    3. Supprime destinations orphelines
    4. Met à jour days_spent
    5. Recalcule visit_order séquentiel
    6. (Background) Geocode les spots et destinations sans coordonnées

    Le geocoding se fait en arrière-plan après la synchronisation.
    """
    sb = _require_supabase()

    # 1. Charger tous les jours du trip avec leur location
    days_res = await asyncio.to_thread(
        lambda: sb.from_("itinerary_days")
            .select("id, destination_id, validated, location")
            .eq("trip_id", trip_id)
            .execute()
    )

    all_days = days_res.data or []
    if not all_days:
        return {"synced": True, "geocoding_scheduled": False}

    invalid_days = [d for d in all_days if not d.get("validated", True)]
    valid_days   = [d for d in all_days if  d.get("validated", True)]

    # 2. Supprimer spots + jours non-validés
    if invalid_days:
        invalid_ids = [d["id"] for d in invalid_days]
        await asyncio.to_thread(
            lambda: sb.from_("spots").delete().in_("itinerary_day_id", invalid_ids).execute()
        )
        await asyncio.to_thread(
            lambda: sb.from_("itinerary_days").delete().in_("id", invalid_ids).execute()
        )

    # 3. Destinations encore référencées
    active_dest_ids = {d["destination_id"] for d in valid_days if d.get("destination_id")}

    # 4. Supprimer destinations orphelines
    dests_res = await asyncio.to_thread(
        lambda: sb.from_("destinations").select("id").eq("trip_id", trip_id).execute()
    )
    all_dest_ids = [d["id"] for d in (dests_res.data or [])]
    orphan_ids = [did for did in all_dest_ids if did not in active_dest_ids]

    if orphan_ids:
        await asyncio.to_thread(
            lambda: sb.from_("destinations").delete().in_("id", orphan_ids).execute()
        )

    # 5. Mettre à jour days_spent
    days_by_dest: Dict[str, int] = {}
    for day in valid_days:
        dest_id = day.get("destination_id")
        if dest_id:
            days_by_dest[dest_id] = days_by_dest.get(dest_id, 0) + 1

    for dest_id, count in days_by_dest.items():
        await asyncio.to_thread(
            lambda did=dest_id, c=count: sb.from_("destinations")
                .update({"days_spent": c})
                .eq("id", did)
                .execute()
        )

    # 6. Recalculer visit_order
    remaining_res = await asyncio.to_thread(
        lambda: sb.from_("destinations")
            .select("id, visit_order")
            .eq("trip_id", trip_id)
            .order("visit_order")
            .execute()
    )

    remaining = remaining_res.data or []
    for i, dest in enumerate(remaining):
        await asyncio.to_thread(
            lambda did=dest["id"], order=i+1: sb.from_("destinations")
                .update({"visit_order": order})
                .eq("id", did)
                .execute()
        )

    # 7. Lancer le geocoding en arrière-plan (non-bloquant)
    background_tasks.add_task(_geocode_review_in_background, trip_id)

    return {"synced": True, "geocoding_scheduled": True}


@router.patch("/spots/{spot_id}", status_code=200)
async def update_spot(
    spot_id: str,
    body: SpotUpdateBody,
    user_id: str = Depends(get_current_user_id),
) -> Dict:
    """Met à jour les champs d'un spot."""
    sb = _require_supabase()
    _check_spot_ownership(sb, spot_id, user_id)
    payload = {k: v for k, v in body.model_dump().items() if v is not None}
    if not payload:
        return {"updated": False}
    sb.from_("spots").update(payload).eq("id", spot_id).execute()
    return {"updated": True}


@router.delete("/spots/{spot_id}", status_code=204)
async def delete_spot(
    spot_id: str,
    user_id: str = Depends(get_current_user_id),
):
    """Supprime un spot."""
    sb = _require_supabase()
    _check_spot_ownership(sb, spot_id, user_id)
    sb.from_("spots").delete().eq("id", spot_id).execute()


@router.patch("/spots/{spot_id}/coordinates", status_code=200)
async def update_spot_coordinates(
    spot_id: str,
    body: CoordinatesBody,
    user_id: str = Depends(get_current_user_id),
) -> Dict:
    """Met à jour latitude/longitude d'un spot."""
    sb = _require_supabase()
    _check_spot_ownership(sb, spot_id, user_id)
    sb.from_("spots") \
        .update({"latitude": body.lat, "longitude": body.lon}) \
        .eq("id", spot_id) \
        .execute()
    return {"updated": True}


@router.patch("/destinations/{dest_id}/coordinates", status_code=200)
async def update_destination_coordinates(
    dest_id: str,
    body: CoordinatesBody,
    user_id: str = Depends(get_current_user_id),
) -> Dict:
    """Met à jour latitude/longitude d'une destination."""
    sb = _require_supabase()
    _check_destination_ownership(sb, dest_id, user_id)
    sb.from_("destinations") \
        .update({"latitude": body.lat, "longitude": body.lon}) \
        .eq("id", dest_id) \
        .execute()
    return {"updated": True}


@router.post("/{trip_id}/add-city", status_code=200)
async def add_city_to_trip(
    trip_id: str,
    body: AddCityToTripBody,
    user_id: str = Depends(get_current_user_id),
) -> Dict:
    """
    Ajoute une city existante à un trip.
    - Si day_id fourni : ajoute les highlights comme spots à ce jour
    - Si create_new_day=true : crée un nouveau jour et ajoute les highlights
    """
    sb = _require_supabase()

    # 1. Récupérer la city et ses highlights
    city_res = sb.from_("cities") \
        .select("id, city_name, country") \
        .eq("id", body.city_id) \
        .maybe_single() \
        .execute()

    if not city_res.data:
        raise HTTPException(404, detail="City introuvable")

    city = city_res.data
    city_name = city["city_name"]
    country = city.get("country", "")

    # 2. Récupérer les highlights de la city
    highlights_res = sb.from_("city_highlights") \
        .select("*") \
        .eq("city_id", body.city_id) \
        .eq("validated", True) \
        .order("highlight_order") \
        .execute()

    highlights = highlights_res.data or []
    if not highlights:
        raise HTTPException(400, detail="Cette city n'a pas de highlights")

    # 3. Déterminer le jour cible
    target_day_id = body.day_id
    destination_id = None

    if body.create_new_day or not body.day_id:
        # Chercher si une destination existe déjà pour cette ville
        dest_res = sb.from_("destinations") \
            .select("id") \
            .eq("trip_id", trip_id) \
            .ilike("city", f"%{city_name}%") \
            .limit(1) \
            .execute()

        if dest_res.data and len(dest_res.data) > 0:
            destination_id = dest_res.data[0]["id"]
        else:
            # Créer une nouvelle destination
            max_order_res = sb.from_("destinations") \
                .select("visit_order") \
                .eq("trip_id", trip_id) \
                .order("visit_order", desc=True) \
                .limit(1) \
                .execute()

            max_order = 0
            if max_order_res.data and len(max_order_res.data) > 0:
                max_order = max_order_res.data[0].get("visit_order", 0)

            new_dest_res = sb.from_("destinations").insert({
                "trip_id": trip_id,
                "city": city_name,
                "country": country,
                "days_spent": 1,
                "visit_order": max_order + 1,
            }).execute()

            if new_dest_res.data:
                destination_id = new_dest_res.data[0]["id"]

        # Créer un nouveau jour
        max_day_res = sb.from_("itinerary_days") \
            .select("day_number") \
            .eq("trip_id", trip_id) \
            .order("day_number", desc=True) \
            .limit(1) \
            .execute()

        max_day = 0
        if max_day_res.data and len(max_day_res.data) > 0:
            max_day = max_day_res.data[0].get("day_number", 0)

        new_day_res = sb.from_("itinerary_days").insert({
            "trip_id": trip_id,
            "destination_id": destination_id,
            "day_number": max_day + 1,
            "location": city_name,
            "theme": f"Découverte de {city_name}",
            "validated": True,
            "linked_city_id": body.city_id,  # Lien pour sync automatique
        }).execute()

        if new_day_res.data:
            target_day_id = new_day_res.data[0]["id"]
    else:
        # Si on ajoute à un jour existant, mettre à jour linked_city_id
        sb.from_("itinerary_days") \
            .update({"linked_city_id": body.city_id}) \
            .eq("id", target_day_id) \
            .execute()

    # 4. Récupérer le max spot_order du jour cible
    max_spot_order = 0
    if target_day_id:
        spot_order_res = sb.from_("spots") \
            .select("spot_order") \
            .eq("itinerary_day_id", target_day_id) \
            .order("spot_order", desc=True) \
            .limit(1) \
            .execute()

        if spot_order_res.data and len(spot_order_res.data) > 0:
            max_spot_order = (spot_order_res.data[0].get("spot_order") or 0) + 1

    # 5. Convertir les highlights en spots et les insérer
    # Mapping des catégories highlight vers les types de spots valides
    # Valid spot_type enum: attraction|restaurant|bar|hotel|activite|transport|shopping
    CATEGORY_TO_SPOT_TYPE = {
        "food": "restaurant",
        "culture": "attraction",
        "nature": "attraction",
        "shopping": "shopping",
        "nightlife": "bar",
        "other": "attraction",
    }

    spots_to_insert = []
    for idx, h in enumerate(highlights):
        category = h.get("category", "other")
        spot_type = CATEGORY_TO_SPOT_TYPE.get(category, "attraction")
        spots_to_insert.append({
            "itinerary_day_id": target_day_id,
            "name": h["name"],
            "spot_type": spot_type,
            "address": h.get("address"),
            "duration_minutes": 60,  # Default
            "price_range": h.get("price_range"),
            "tips": h.get("tips"),
            "highlight": h.get("is_must_see", False),
            "spot_order": max_spot_order + idx,
            "latitude": h.get("latitude"),
            "longitude": h.get("longitude"),
            # Lien vers le highlight source pour synchronisation
            "city_highlight_id": h["id"],
            "source_city_id": body.city_id,
        })

    if spots_to_insert:
        sb.from_("spots").insert(spots_to_insert).execute()

    return {
        "added": True,
        "spots_count": len(spots_to_insert),
        "day_id": target_day_id,
        "city_name": city_name,
    }


@router.post("/{trip_id}/add-destination", status_code=200)
async def add_destination_to_trip(
    trip_id: str,
    body: AddDestinationBody,
    user_id: str = Depends(get_current_user_id),
) -> Dict:
    """
    Ajoute une nouvelle destination (ville saisie manuellement) à un trip.
    1. Vérifie que le trip existe
    2. Vérifie qu'il n'y a pas déjà une destination avec ce nom
    3. Crée la destination avec coordonnées
    4. Crée un jour vide lié à cette destination
    Retourne { added, destination_id, day_id, city_name }
    """
    sb = _require_supabase()

    # 1. Vérifier que le trip existe
    trip_res = await asyncio.to_thread(
        lambda: sb.from_("trips")
            .select("id")
            .eq("id", trip_id)
            .maybe_single()
            .execute()
    )
    if not trip_res.data:
        raise HTTPException(404, detail="Trip introuvable")

    city_name = body.city_name.strip()

    # 2. Vérifier doublon (insensible à la casse)
    existing_res = await asyncio.to_thread(
        lambda: sb.from_("destinations")
            .select("id")
            .eq("trip_id", trip_id)
            .ilike("city", city_name)
            .limit(1)
            .execute()
    )
    if existing_res.data and len(existing_res.data) > 0:
        raise HTTPException(409, detail=f"La destination '{city_name}' existe déjà dans cet itinéraire")

    # 3. Calculer le prochain visit_order
    max_order_res = await asyncio.to_thread(
        lambda: sb.from_("destinations")
            .select("visit_order")
            .eq("trip_id", trip_id)
            .order("visit_order", desc=True)
            .limit(1)
            .execute()
    )
    max_order = 0
    if max_order_res.data and len(max_order_res.data) > 0:
        max_order = max_order_res.data[0].get("visit_order") or 0

    # 4. Créer la destination
    new_dest_res = await asyncio.to_thread(
        lambda: sb.from_("destinations").insert({
            "trip_id": trip_id,
            "city": city_name,
            "country": body.country,
            "days_spent": 1,
            "visit_order": max_order + 1,
            "latitude": body.latitude,
            "longitude": body.longitude,
        }).execute()
    )
    if not new_dest_res.data:
        raise HTTPException(500, detail="Erreur lors de la création de la destination")

    destination_id = new_dest_res.data[0]["id"]

    # 5. Calculer le prochain day_number
    max_day_res = await asyncio.to_thread(
        lambda: sb.from_("itinerary_days")
            .select("day_number")
            .eq("trip_id", trip_id)
            .order("day_number", desc=True)
            .limit(1)
            .execute()
    )
    max_day = 0
    if max_day_res.data and len(max_day_res.data) > 0:
        max_day = max_day_res.data[0].get("day_number") or 0

    # 6. Créer un jour vide lié à la nouvelle destination
    new_day_res = await asyncio.to_thread(
        lambda: sb.from_("itinerary_days").insert({
            "trip_id": trip_id,
            "destination_id": destination_id,
            "day_number": max_day + 1,
            "location": city_name,
            "theme": f"Découverte de {city_name}",
            "validated": True,
        }).execute()
    )
    if not new_day_res.data:
        raise HTTPException(500, detail="Erreur lors de la création du jour")

    day_id = new_day_res.data[0]["id"]

    return {
        "added": True,
        "destination_id": destination_id,
        "day_id": day_id,
        "city_name": city_name,
    }


@router.delete("/{trip_id}/destinations/{dest_id}", status_code=204)
async def delete_destination(
    trip_id: str,
    dest_id: str,
    user_id: str = Depends(get_current_user_id),
):
    """
    Supprime une destination et tous ses jours/spots liés, puis recalcule visit_order.
    """
    sb = _require_supabase()

    # 1. Vérifier que la destination appartient au trip
    dest_res = await asyncio.to_thread(
        lambda: sb.from_("destinations")
            .select("id")
            .eq("id", dest_id)
            .eq("trip_id", trip_id)
            .maybe_single()
            .execute()
    )
    if not dest_res.data:
        raise HTTPException(404, detail="Destination introuvable")

    # 2. Récupérer les jours liés à cette destination
    days_res = await asyncio.to_thread(
        lambda: sb.from_("itinerary_days")
            .select("id")
            .eq("trip_id", trip_id)
            .eq("destination_id", dest_id)
            .execute()
    )
    day_ids = [d["id"] for d in (days_res.data or [])]

    # 3. Supprimer les spots de ces jours
    if day_ids:
        await asyncio.to_thread(
            lambda: sb.from_("spots").delete().in_("itinerary_day_id", day_ids).execute()
        )
        # 4. Supprimer les jours
        await asyncio.to_thread(
            lambda: sb.from_("itinerary_days").delete().in_("id", day_ids).execute()
        )

    # 5. Supprimer la destination
    await asyncio.to_thread(
        lambda: sb.from_("destinations").delete().eq("id", dest_id).execute()
    )

    # 6. Recalculer visit_order des destinations restantes
    # On fait les updates séquentiellement pour éviter les conflits de contrainte d'unicité
    remaining_res = await asyncio.to_thread(
        lambda: sb.from_("destinations")
            .select("id, visit_order")
            .eq("trip_id", trip_id)
            .order("visit_order")
            .execute()
    )
    remaining = remaining_res.data or []

    # D'abord, mettre des valeurs négatives temporaires pour éviter les conflits
    for i, dest in enumerate(remaining):
        await asyncio.to_thread(
            lambda d=dest, idx=i: sb.from_("destinations")
                .update({"visit_order": -(idx + 1)})
                .eq("id", d["id"])
                .execute()
        )

    # Ensuite, mettre les vraies valeurs
    for i, dest in enumerate(remaining):
        await asyncio.to_thread(
            lambda d=dest, idx=i: sb.from_("destinations")
                .update({"visit_order": idx + 1})
                .eq("id", d["id"])
                .execute()
        )


@router.patch("/{trip_id}/destinations/reorder", status_code=200)
async def reorder_destinations(
    trip_id: str,
    body: ReorderDestinationsBody,
    user_id: str = Depends(get_current_user_id),
) -> Dict:
    """Met à jour visit_order de chaque destination et réordonne les itinerary_days."""
    sb = _require_supabase()

    try:
        # 0. Vérifier que les destinations appartiennent au trip
        dest_ids = [d.id for d in body.destinations]
        if dest_ids:
            check_res = await asyncio.to_thread(
                lambda: sb.from_("destinations")
                    .select("id")
                    .eq("trip_id", trip_id)
                    .in_("id", dest_ids)
                    .execute()
            )
            found_ids = {d["id"] for d in (check_res.data or [])}
            missing = set(dest_ids) - found_ids
            if missing:
                raise HTTPException(404, detail=f"Destinations introuvables: {missing}")

        # 1. Mettre à jour visit_order des destinations
        # D'abord mettre des valeurs temporaires négatives pour éviter les conflits d'unicité
        if body.destinations:
            for i, dest in enumerate(body.destinations):
                await asyncio.to_thread(
                    lambda d=dest, idx=i: sb.from_("destinations")
                        .update({"visit_order": -(idx + 1000)})
                        .eq("id", d.id)
                        .eq("trip_id", trip_id)
                        .execute()
                )
            # Puis mettre les vraies valeurs
            for dest in body.destinations:
                await asyncio.to_thread(
                    lambda d=dest: sb.from_("destinations")
                        .update({"visit_order": d.order})
                        .eq("id", d.id)
                        .eq("trip_id", trip_id)
                        .execute()
                )

        # 2. Récupérer tous les jours du trip
        days_res = await asyncio.to_thread(
            lambda: sb.from_("itinerary_days")
                .select("id, destination_id, day_number")
                .eq("trip_id", trip_id)
                .execute()
        )
        all_days = days_res.data or []

        if not all_days:
            return {"reordered": True}

        # 3. Créer un mapping destination_id -> visit_order
        dest_order_map = {dest.id: dest.order for dest in body.destinations}

        # 4. Grouper les jours par destination_id et trier par day_number interne
        from collections import defaultdict
        days_by_dest: Dict[str, list] = defaultdict(list)
        for day in all_days:
            dest_id = day.get("destination_id")
            if dest_id:
                days_by_dest[dest_id].append(day)

        # Trier chaque groupe par day_number existant (pour préserver l'ordre relatif interne)
        for dest_id in days_by_dest:
            days_by_dest[dest_id].sort(key=lambda d: d.get("day_number", 0))

        # 5. Reconstruire l'ordre global des jours selon le nouvel ordre des destinations
        sorted_dest_ids = sorted(
            days_by_dest.keys(),
            key=lambda did: dest_order_map.get(did, 999)
        )

        new_day_order = []
        for dest_id in sorted_dest_ids:
            new_day_order.extend(days_by_dest[dest_id])

        # 6. Mettre à jour day_number de chaque jour
        if new_day_order:
            await asyncio.gather(*[
                asyncio.to_thread(
                    lambda day_id=day["id"], new_num=idx + 1: sb.from_("itinerary_days")
                        .update({"day_number": new_num})
                        .eq("id", day_id)
                        .execute()
                )
                for idx, day in enumerate(new_day_order)
            ])

        return {"reordered": True}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error reordering destinations for trip {trip_id}: {e}")
        raise HTTPException(500, detail=f"Erreur lors du réordonnancement: {str(e)}")
