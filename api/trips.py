"""
Routes pour la gestion des trips (voyages)
"""
import logging
import asyncio
from typing import List, Dict, Optional, Tuple
from collections import defaultdict
from fastapi import APIRouter, HTTPException, Depends, Query, BackgroundTasks
from pydantic import BaseModel

from utils.auth import get_current_user_id
from services.supabase_service import SupabaseService
from services.geocoding_service import batch_geocode_spots, batch_geocode_destinations
from models.errors import ErrorCode, get_error_message

logger = logging.getLogger("bombo.api.trips")

router = APIRouter(prefix="/trips", tags=["trips"])

_supabase_service: SupabaseService = None


def set_supabase_service(service: SupabaseService):
    global _supabase_service
    _supabase_service = service


def _require_supabase():
    if not _supabase_service or not _supabase_service.is_configured():
        raise HTTPException(503, detail={
            "error_code": ErrorCode.SERVICE_UNAVAILABLE,
            "message": get_error_message(ErrorCode.SERVICE_UNAVAILABLE),
        })
    return _supabase_service.supabase_client


# ── Modèles ───────────────────────────────────────────────────────────────────

class SaveTripBody(BaseModel):
    notes: Optional[str] = None


class CreateManualTripBody(BaseModel):
    title: Optional[str] = None
    use_template: bool = True


# ── Routes ────────────────────────────────────────────────────────────────────

@router.post("/create-manual", status_code=201)
async def create_manual_trip(
    body: CreateManualTripBody = CreateManualTripBody(),
    user_id: str = Depends(get_current_user_id),
) -> Dict:
    """
    Crée un trip manuellement.
    Si use_template=True, utilise le template pré-rempli (Paris 2 jours).
    Si use_template=False, crée un trip vide.
    Le trip n'est PAS auto-sauvegardé - l'utilisateur doit le sauvegarder depuis l'écran review.
    """
    _require_supabase()
    trip_id = await _supabase_service.create_manual_trip(user_id, body.title, body.use_template)
    if not trip_id:
        raise HTTPException(500, detail={
            "error_code": ErrorCode.EXTERNAL_SERVICE_ERROR,
            "message": "Impossible de créer le trip",
        })
    return {"trip_id": trip_id, "message": "Trip créé"}



@router.get("/public")
async def get_public_trips(limit: int = 20) -> List[Dict]:
    """Trips publics (feed de découverte)."""
    sb = _require_supabase()
    res = sb.from_("trip_details") \
        .select("*") \
        .eq("is_public", True) \
        .order("created_at", desc=True) \
        .limit(limit) \
        .execute()
    return res.data or []


@router.get("/saved")
async def get_saved_trips(user_id: str = Depends(get_current_user_id)) -> List[Dict]:
    """Tous les trips sauvegardés par l'utilisateur."""
    sb = _require_supabase()
    res = sb.from_("user_saved_trips") \
        .select("id, notes, created_at, trips(*)") \
        .eq("user_id", user_id) \
        .order("created_at", desc=True) \
        .execute()
    return res.data or []


# ── Unified Saved Endpoint ────────────────────────────────────────────────────
# IMPORTANT: Must be defined BEFORE /{trip_id} to avoid route conflicts

@router.get("/saved/all")
async def get_unified_saved(
    user_id: str = Depends(get_current_user_id),
    item_type: str = Query("all", alias="type", regex="^(all|trip|city)$"),
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
) -> Dict:
    """
    Retourne tous les items sauvegardés (trips et/ou cities) avec pagination.
    Filter: all | trip | city
    """
    sb = _require_supabase()
    offset = (page - 1) * limit
    # On récupère offset+limit de chaque table — suffisant pour reconstruire la page correctement
    db_limit = offset + limit
    items = []
    any_table_hit_limit = False

    # Récupérer les trips sauvegardés si demandé
    trip_days_map = {}  # trip_id -> [{"day_number": X, "spots_count": Y}, ...]
    if item_type in ("all", "trip"):
        trips_res = sb.from_("user_saved_trips") \
            .select("id, notes, created_at, trips(id, trip_title, vibe, duration_days, thumbnail_url, source_url, content_creator_handle)") \
            .eq("user_id", user_id) \
            .order("created_at", desc=True) \
            .limit(db_limit) \
            .execute()
        if len(trips_res.data or []) == db_limit:
            any_table_hit_limit = True

        # Collecter les trip_ids pour requête groupée
        trip_entity_ids = [trip["id"] for row in (trips_res.data or []) if (trip := row.get("trips"))]

        # Requête groupée pour spots par jour
        if trip_entity_ids:
            days_res = sb.from_("itinerary_days") \
                .select("trip_id, day_number, spots(id)") \
                .in_("trip_id", trip_entity_ids) \
                .eq("validated", True) \
                .execute()

            # Grouper par trip_id, puis par day_number, compter les spots
            trip_days_raw = defaultdict(lambda: defaultdict(int))
            for day_row in (days_res.data or []):
                tid = day_row.get("trip_id")
                day_num = day_row.get("day_number")
                spots = day_row.get("spots") or []
                trip_days_raw[tid][day_num] += len(spots)

            # Convertir en format final
            for tid, days_dict in trip_days_raw.items():
                trip_days_map[tid] = [
                    {"day_number": d, "spots_count": c}
                    for d, c in sorted(days_dict.items())
                ]

        for row in (trips_res.data or []):
            trip = row.get("trips")
            if trip:
                items.append({
                    "id": row["id"],
                    "entity_type": "trip",
                    "entity_id": trip["id"],
                    "title": trip.get("trip_title", "Sans titre"),
                    "subtitle": trip.get("vibe") or "",
                    "thumbnail_url": trip.get("thumbnail_url"),
                    "vibe": trip.get("vibe"),
                    "duration_days": trip.get("duration_days"),
                    "highlights_count": None,
                    "created_at": row["created_at"],
                    "notes": row.get("notes"),
                    "source_url": trip.get("source_url"),
                    "content_creator_handle": trip.get("content_creator_handle"),
                    "days": trip_days_map.get(trip["id"], []),
                })

    # Récupérer les cities sauvegardées si demandé
    city_categories_map = {}  # city_id -> [{"category": X, "count": Y}, ...]
    if item_type in ("all", "city"):
        # city_details contient déjà highlights_count — une seule requête au lieu de 1+N
        cities_res = sb.from_("user_saved_cities") \
            .select("id, notes, created_at, city_details(id, city_title, city_name, country, vibe_tags, thumbnail_url, source_url, content_creator_handle, highlights_count)") \
            .eq("user_id", user_id) \
            .order("created_at", desc=True) \
            .limit(db_limit) \
            .execute()
        if len(cities_res.data or []) == db_limit:
            any_table_hit_limit = True

        # Collecter les city_ids pour requête groupée
        city_entity_ids = [city["id"] for row in (cities_res.data or []) if (city := row.get("city_details"))]

        # Requête groupée pour highlights par catégorie
        if city_entity_ids:
            highlights_res = sb.from_("city_highlights") \
                .select("city_id, category") \
                .in_("city_id", city_entity_ids) \
                .eq("validated", True) \
                .execute()

            # Grouper par city_id, puis par category, compter
            city_cats_raw = defaultdict(lambda: defaultdict(int))
            for hl_row in (highlights_res.data or []):
                cid = hl_row.get("city_id")
                cat = hl_row.get("category")
                if cat:
                    city_cats_raw[cid][cat] += 1

            # Convertir en format final
            for cid, cats_dict in city_cats_raw.items():
                city_categories_map[cid] = [
                    {"category": c, "count": cnt}
                    for c, cnt in sorted(cats_dict.items(), key=lambda x: -x[1])  # tri par count décroissant
                ]

        for row in (cities_res.data or []):
            city = row.get("city_details")
            if city:
                items.append({
                    "id": row["id"],
                    "entity_type": "city",
                    "entity_id": city["id"],
                    "title": city.get("city_title", "Sans titre"),
                    "subtitle": f"{city.get('city_name', '')}, {city.get('country', '')}".strip(", "),
                    "thumbnail_url": city.get("thumbnail_url"),
                    "vibe": city.get("vibe_tags", [None])[0] if city.get("vibe_tags") else None,
                    "duration_days": None,
                    "highlights_count": city.get("highlights_count") or 0,
                    "created_at": row["created_at"],
                    "notes": row.get("notes"),
                    "source_url": city.get("source_url"),
                    "content_creator_handle": city.get("content_creator_handle"),
                    "categories": city_categories_map.get(city["id"], []),
                })

    # Trier par date de création (plus récent en premier)
    items.sort(key=lambda x: x["created_at"], reverse=True)

    # Pagination
    paginated_items = items[offset:offset + limit]

    return {
        "items": paginated_items,
        "page": page,
        "limit": limit,
        "total": len(items),
        "has_more": offset + limit < len(items) or any_table_hit_limit,
    }



@router.get("/{trip_id}/saved")
async def is_trip_saved(
    trip_id: str,
    user_id: str = Depends(get_current_user_id),
) -> Dict:
    """Vérifie si un trip est sauvegardé par l'utilisateur."""
    sb = _require_supabase()
    try:
        res = sb.from_("user_saved_trips") \
            .select("id") \
            .eq("user_id", user_id) \
            .eq("trip_id", trip_id) \
            .maybe_single() \
            .execute()
        return {"saved": res is not None and res.data is not None}
    except Exception:
        return {"saved": False}


@router.get("/{trip_id}")
async def get_trip(trip_id: str) -> Dict:
    """Récupère les détails d'un voyage par son ID."""
    if not _supabase_service or not _supabase_service.is_configured():
        raise HTTPException(503, detail={
            "error_code": ErrorCode.SERVICE_UNAVAILABLE,
            "message": get_error_message(ErrorCode.SERVICE_UNAVAILABLE),
        })

    trip = await _supabase_service.get_trip(trip_id)
    if not trip:
        raise HTTPException(404, detail={
            "error_code": ErrorCode.TRIP_NOT_FOUND,
            "message": get_error_message(ErrorCode.TRIP_NOT_FOUND),
        })

    return trip


@router.delete("/{trip_id}", status_code=204)
async def delete_trip(
    trip_id: str,
    user_id: str = Depends(get_current_user_id),
):
    """Supprime un trip et son analysis_job associé."""
    sb = _require_supabase()
    # Vérifier ownership et récupérer job_id
    res = sb.from_("trips").select("id, job_id").eq("id", trip_id).eq("user_id", user_id).maybe_single().execute()
    if not res.data:
        raise HTTPException(404, detail={
            "error_code": ErrorCode.TRIP_NOT_FOUND,
            "message": get_error_message(ErrorCode.TRIP_NOT_FOUND),
        })

    job_id = res.data.get("job_id")

    # Supprimer le trip
    sb.from_("trips").delete().eq("id", trip_id).execute()

    # Supprimer l'analysis_job associé si présent
    if job_id:
        sb.from_("analysis_jobs").delete().eq("id", job_id).execute()


@router.post("/{trip_id}/save", status_code=201)
async def save_trip(
    trip_id: str,
    body: SaveTripBody = SaveTripBody(),
    user_id: str = Depends(get_current_user_id),
) -> Dict:
    """Sauvegarde un trip pour l'utilisateur (idempotent)."""
    sb = _require_supabase()
    payload = {"user_id": user_id, "trip_id": trip_id}
    if body.notes:
        payload["notes"] = body.notes
    sb.from_("user_saved_trips") \
        .upsert(payload, on_conflict="user_id,trip_id", ignore_duplicates=True) \
        .execute()
    return {"saved": True}


@router.delete("/{trip_id}/save", status_code=204)
async def unsave_trip(
    trip_id: str,
    user_id: str = Depends(get_current_user_id),
):
    """Retire un trip des sauvegardes."""
    sb = _require_supabase()
    sb.from_("user_saved_trips") \
        .delete() \
        .eq("user_id", user_id) \
        .eq("trip_id", trip_id) \
        .execute()


async def _geocode_trip_in_background(trip_id: str) -> None:
    """
    Background task to geocode all spots and destinations for a trip.
    This runs AFTER the trip has been saved, so it doesn't block the UI.
    """
    if not _supabase_service or not _supabase_service.is_configured():
        logger.warning("Supabase not configured for background geocoding")
        return

    sb = _supabase_service.supabase_client
    geocoded_spots = 0

    try:
        logger.info(f"[GEOCODE] Starting background geocoding for trip {trip_id}")

        # Récupérer les jours validés avec leur location et destination
        days_res = sb.from_("itinerary_days") \
            .select("id, location, destination_id") \
            .eq("trip_id", trip_id) \
            .eq("validated", True) \
            .execute()
        valid_days = days_res.data or []

        logger.info(f"[GEOCODE] Found {len(valid_days)} valid days for trip {trip_id}")

        if not valid_days:
            logger.warning(f"[GEOCODE] No valid days found for trip {trip_id}, skipping geocoding")
            return

        valid_day_ids = [d["id"] for d in valid_days]
        day_location_map = {d["id"]: d.get("location") for d in valid_days}
        day_dest_map = {d["id"]: d.get("destination_id") for d in valid_days}

        # Récupérer la destination pour avoir le context ville/pays
        dest_res = sb.from_("destinations") \
            .select("id, city, country") \
            .eq("trip_id", trip_id) \
            .execute()
        destinations = dest_res.data or []
        
        # Créer un mapping destination_id -> (city, country) pour fallback
        dest_location_map: Dict[str, str] = {}
        for dest in destinations:
            if dest.get("city"):
                country = dest.get("country") or ""
                context = f"{dest['city']}, {country}" if country else dest["city"]
                dest_location_map[dest["id"]] = context
        
        # Créer un mapping day_id -> destination context pour les spots
        day_country_map: Dict[str, str] = {}
        for day_id, dest_id in day_dest_map.items():
            if dest_id and dest_id in dest_location_map:
                day_country_map[day_id] = dest_location_map[dest_id]
        
        logger.info(f"[GEOCODE] Found {len(destinations)} destinations: {dest_location_map}")
        logger.info(f"[GEOCODE] Day -> Country map: {day_country_map}")

        # Récupérer et geocoder les spots sans coordonnées
        spots_res = sb.from_("spots") \
            .select("id, name, address, itinerary_day_id") \
            .in_("itinerary_day_id", valid_day_ids) \
            .execute()
        all_spots = spots_res.data or []
        logger.info(f"[GEOCODE] Found {len(all_spots)} spots for {len(valid_day_ids)} days")
        
        # Log des spots pour debug
        for spot in all_spots[:3]:  # Log first 3 spots
            logger.info(f"[GEOCODE] Spot '{spot.get('name')}'")

        async def update_spot_coords(spot_id: str, lat: float, lon: float):
            await asyncio.to_thread(
                lambda: sb.from_("spots")
                    .update({"latitude": lat, "longitude": lon})
                    .eq("id", spot_id)
                    .execute()
            )

        # Grouper les spots par location context (via le jour) avec fallback pays
        spots_by_location: Dict[str, Tuple[List, Optional[str]]] = {}  # location -> (spots, fallback_country)
        spots_without_location = 0
        for spot in all_spots:
            day_id = spot.get("itinerary_day_id")
            location = day_location_map.get(day_id)
            fallback_country = day_country_map.get(day_id)  # Pays de la destination
            
            if location:
                if location not in spots_by_location:
                    spots_by_location[location] = ([], fallback_country)
                spots_by_location[location][0].append(spot)
            else:
                spots_without_location += 1
        
        if spots_without_location > 0:
            logger.warning(f"[GEOCODE] {spots_without_location} spots skipped (no location context)")

        for location, (spots, fallback_country) in spots_by_location.items():
            results = await batch_geocode_spots(
                spots=spots,
                location=location,
                fallback_country=fallback_country,
                update_callback=update_spot_coords,
            )
            geocoded_spots += len(results)

        logger.info(
            f"Background geocoding complete for trip {trip_id}: "
            f"{geocoded_spots} spots geocoded"
        )

    except Exception as e:
        logger.error(f"Background geocoding failed for trip {trip_id}: {e}")


@router.post("/{trip_id}/validate-and-save", status_code=200)
async def validate_and_save_trip(
    trip_id: str,
    background_tasks: BackgroundTasks,
    body: SaveTripBody = SaveTripBody(),
    user_id: str = Depends(get_current_user_id),
) -> Dict:
    """
    Valide et sauvegarde un trip de manière atomique (transactionnelle).

    Cette opération combine syncDestinations + saveTrip en une seule transaction:
    1. Supprime les spots des jours non-validés
    2. Supprime les jours non-validés
    3. Supprime les destinations orphelines
    4. Met à jour days_spent
    5. Recalcule visit_order
    6. Sauvegarde le trip pour l'utilisateur
    7. (Background) Geocode les spots et destinations sans coordonnées

    Si une étape échoue, toutes les modifications sont annulées (rollback).
    Le geocoding se fait en arrière-plan après la sauvegarde.
    """
    sb = _require_supabase()

    try:
        # Appel de la fonction RPC PostgreSQL qui garantit l'atomicité
        result = sb.rpc(
            "validate_and_save_trip",
            {
                "p_trip_id": trip_id,
                "p_user_id": user_id,
                "p_notes": body.notes,
            }
        ).execute()

        # Lancer le geocoding en arrière-plan (non-bloquant)
        logger.info(f"[VALIDATE] Scheduling background geocoding for trip {trip_id}")
        background_tasks.add_task(_geocode_trip_in_background, trip_id)

        if result.data:
            response = result.data
            if isinstance(response, dict):
                response["geocoding_scheduled"] = True
            return response

        return {"success": True, "synced": True, "saved": True, "geocoding_scheduled": True}

    except Exception as e:
        logger.error(f"validate_and_save_trip failed for trip {trip_id}: {e}")
        raise HTTPException(500, detail={
            "error_code": ErrorCode.EXTERNAL_SERVICE_ERROR,
            "message": f"Erreur lors de la validation du trip: {str(e)}",
        })
