"""
Routes pour la gestion des trips (voyages)
"""
import logging
from typing import List, Dict, Optional
from collections import defaultdict
from fastapi import APIRouter, HTTPException, Depends, Query
from pydantic import BaseModel

from utils.auth import get_current_user_id
from services.supabase_service import SupabaseService
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


# ── Routes ────────────────────────────────────────────────────────────────────

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
