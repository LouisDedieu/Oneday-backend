"""
Routes pour la gestion des cities (guides de ville)
"""
import logging
from typing import List, Dict, Optional
from fastapi import APIRouter, HTTPException, Depends, Query
from pydantic import BaseModel

from utils.auth import get_current_user_id
from services.supabase_service import SupabaseService

logger = logging.getLogger("bombo.api.cities")

router = APIRouter(prefix="/cities", tags=["cities"])

_supabase_service: SupabaseService = None


def set_supabase_service(service: SupabaseService):
    global _supabase_service
    _supabase_service = service


def _require_supabase():
    if not _supabase_service or not _supabase_service.is_configured():
        raise HTTPException(503, detail="Supabase non configure")
    return _supabase_service.supabase_client


# -- Modeles -------------------------------------------------------------------

class SaveCityBody(BaseModel):
    notes: Optional[str] = None


class MergeCityBody(BaseModel):
    source_city_id: str
    highlight_ids: Optional[List[str]] = None  # If provided, only merge these highlights
    delete_source: bool = True  # Delete source city after merge


# -- Routes --------------------------------------------------------------------

@router.get("/public")
async def get_public_cities(limit: int = 20) -> List[Dict]:
    """Cities publiques (feed de decouverte)."""
    sb = _require_supabase()
    res = sb.from_("city_details") \
        .select("*") \
        .eq("is_public", True) \
        .order("created_at", desc=True) \
        .limit(limit) \
        .execute()
    return res.data or []


@router.get("/saved")
async def get_saved_cities(
    user_id: str = Depends(get_current_user_id),
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
) -> Dict:
    """Toutes les cities sauvegardees par l'utilisateur avec pagination."""
    sb = _require_supabase()
    offset = (page - 1) * limit

    res = sb.from_("user_saved_cities") \
        .select("id, notes, created_at, cities(*)") \
        .eq("user_id", user_id) \
        .order("created_at", desc=True) \
        .range(offset, offset + limit - 1) \
        .execute()

    items = res.data or []
    return {
        "items": items,
        "page": page,
        "limit": limit,
        "has_more": len(items) == limit
    }


@router.get("/match")
async def check_city_match(
    city_name: str = Query(...),
    user_id: str = Depends(get_current_user_id),
) -> Dict:
    """Verifie si une city avec ce nom existe deja pour l'utilisateur (pour fusion)."""
    sb = _require_supabase()

    logger.info(f"[Merge] Checking match for city_name='{city_name}', user_id={user_id}")

    # Use limit(1) instead of maybe_single() to avoid error when multiple cities exist
    res = sb.from_("cities") \
        .select("id, city_name, city_title") \
        .eq("user_id", user_id) \
        .ilike("city_name", city_name) \
        .order("created_at", desc=False) \
        .limit(1) \
        .execute()

    # Get first result if exists
    first_match = res.data[0] if res.data else None
    logger.info(f"[Merge] Query result: {first_match}")

    if first_match:
        # Compter les highlights
        highlights_res = sb.from_("city_highlights") \
            .select("id", count="exact") \
            .eq("city_id", first_match["id"]) \
            .execute()

        return {
            "match": True,
            "city_id": first_match["id"],
            "city_name": first_match["city_name"],
            "city_title": first_match["city_title"],
            "highlights_count": highlights_res.count or 0
        }

    return {"match": False}


@router.get("/{city_id}/saved")
async def is_city_saved(
    city_id: str,
    user_id: str = Depends(get_current_user_id),
) -> Dict:
    """Verifie si une city est sauvegardee par l'utilisateur."""
    sb = _require_supabase()
    try:
        res = sb.from_("user_saved_cities") \
            .select("id") \
            .eq("user_id", user_id) \
            .eq("city_id", city_id) \
            .maybe_single() \
            .execute()
        return {"saved": res is not None and res.data is not None}
    except Exception:
        return {"saved": False}


@router.get("/{city_id}")
async def get_city(city_id: str) -> Dict:
    """Recupere les details d'une city par son ID avec toutes ses relations."""
    if not _supabase_service or not _supabase_service.is_configured():
        raise HTTPException(503, detail="Supabase non configure")

    city = await _supabase_service.get_city(city_id)
    if not city:
        raise HTTPException(404, detail="City introuvable")

    return city


@router.delete("/{city_id}", status_code=204)
async def delete_city(
    city_id: str,
    user_id: str = Depends(get_current_user_id),
):
    """Supprime une city et son analysis_job associe."""
    sb = _require_supabase()

    # Verifier ownership et recuperer job_id
    res = sb.from_("cities") \
        .select("id, job_id") \
        .eq("id", city_id) \
        .eq("user_id", user_id) \
        .maybe_single() \
        .execute()

    if not res.data:
        raise HTTPException(404, detail="City introuvable ou acces refuse")

    job_id = res.data.get("job_id")

    # 1. Supprimer les user_saved_cities references
    sb.from_("user_saved_cities").delete().eq("city_id", city_id).execute()

    # 2. Nullifier city_id dans analysis_jobs pour eviter FK violation
    sb.from_("analysis_jobs").update({"city_id": None}).eq("city_id", city_id).execute()

    # 3. Supprimer la city (cascade supprime highlights, budget, practical_info)
    sb.from_("cities").delete().eq("id", city_id).execute()

    # 4. Supprimer l'analysis_job associe si present
    if job_id:
        sb.from_("analysis_jobs").delete().eq("id", job_id).execute()


@router.post("/{city_id}/save", status_code=201)
async def save_city(
    city_id: str,
    body: SaveCityBody = SaveCityBody(),
    user_id: str = Depends(get_current_user_id),
) -> Dict:
    """Sauvegarde une city pour l'utilisateur (idempotent)."""
    sb = _require_supabase()
    payload = {"user_id": user_id, "city_id": city_id}
    if body.notes:
        payload["notes"] = body.notes
    sb.from_("user_saved_cities") \
        .upsert(payload, on_conflict="user_id,city_id", ignore_duplicates=True) \
        .execute()
    return {"saved": True}


@router.delete("/{city_id}/save", status_code=204)
async def unsave_city(
    city_id: str,
    user_id: str = Depends(get_current_user_id),
):
    """Retire une city des sauvegardes."""
    sb = _require_supabase()
    sb.from_("user_saved_cities") \
        .delete() \
        .eq("user_id", user_id) \
        .eq("city_id", city_id) \
        .execute()


@router.post("/{city_id}/merge", status_code=200)
async def merge_cities(
    city_id: str,
    body: MergeCityBody,
    user_id: str = Depends(get_current_user_id),
) -> Dict:
    """
    Fusionne les highlights d'une source city vers la target city.
    Les highlights sont copies avec un nouvel ordre.
    Optionally only merges specific highlights and deletes source city.
    """
    logger.info(f"[Merge] Request: target={city_id}, source={body.source_city_id}, highlight_ids={body.highlight_ids}, delete_source={body.delete_source}")
    sb = _require_supabase()

    # Verifier ownership des deux cities
    target_res = sb.from_("cities") \
        .select("id") \
        .eq("id", city_id) \
        .eq("user_id", user_id) \
        .maybe_single() \
        .execute()

    source_res = sb.from_("cities") \
        .select("id, job_id") \
        .eq("id", body.source_city_id) \
        .eq("user_id", user_id) \
        .maybe_single() \
        .execute()

    if not target_res.data or not source_res.data:
        raise HTTPException(404, detail="City introuvable ou acces refuse")

    source_job_id = source_res.data.get("job_id")

    # Recuperer le max order actuel de la target
    max_order_res = sb.from_("city_highlights") \
        .select("highlight_order") \
        .eq("city_id", city_id) \
        .order("highlight_order", desc=True) \
        .limit(1) \
        .execute()

    current_max = max_order_res.data[0]["highlight_order"] if max_order_res.data else 0

    # Recuperer les highlights de la source
    query = sb.from_("city_highlights") \
        .select("*") \
        .eq("city_id", body.source_city_id)

    # Filter by specific highlight IDs if provided
    if body.highlight_ids:
        logger.info(f"[Merge] Filtering to {len(body.highlight_ids)} highlight IDs: {body.highlight_ids}")
        query = query.in_("id", body.highlight_ids)
    else:
        logger.info("[Merge] No highlight_ids provided, merging ALL highlights")

    source_highlights_res = query.order("highlight_order").execute()

    source_highlights = source_highlights_res.data or []
    logger.info(f"[Merge] Found {len(source_highlights)} highlights to merge")
    merged_count = 0

    # Copier chaque highlight vers la target city
    for idx, h in enumerate(source_highlights):
        new_highlight = {
            "city_id": city_id,
            "name": h["name"],
            "category": h["category"],
            "subtype": h.get("subtype"),
            "address": h.get("address"),
            "description": h.get("description"),
            "price_range": h.get("price_range"),
            "tips": h.get("tips"),
            "is_must_see": h.get("is_must_see", False),
            "latitude": h.get("latitude"),
            "longitude": h.get("longitude"),
            "highlight_order": current_max + idx + 1,
            "validated": True,
        }
        sb.from_("city_highlights").insert(new_highlight).execute()
        merged_count += 1

    # Delete source city if requested (removes from inbox too)
    if body.delete_source:
        # 1. Delete user_saved_cities references
        sb.from_("user_saved_cities").delete().eq("city_id", body.source_city_id).execute()

        # 2. Nullify city_id in analysis_jobs
        sb.from_("analysis_jobs").update({"city_id": None}).eq("city_id", body.source_city_id).execute()

        # 3. Delete the source city (cascade deletes highlights, budget, practical_info)
        sb.from_("cities").delete().eq("id", body.source_city_id).execute()

        # 4. Delete the analysis_job if present
        if source_job_id:
            sb.from_("analysis_jobs").delete().eq("id", source_job_id).execute()

    return {
        "merged": True,
        "highlights_merged": merged_count,
        "source_deleted": body.delete_source
    }
