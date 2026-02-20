"""
Routes pour la gestion des trips (voyages)
"""
import logging
from typing import List, Dict, Optional
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel

from utils.auth import get_current_user_id
from services.supabase_service import SupabaseService

logger = logging.getLogger("bombo.api.trips")

router = APIRouter(prefix="/trips", tags=["trips"])

_supabase_service: SupabaseService = None


def set_supabase_service(service: SupabaseService):
    global _supabase_service
    _supabase_service = service


def _require_supabase():
    if not _supabase_service or not _supabase_service.is_configured():
        raise HTTPException(503, detail="Supabase non configuré")
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


@router.get("/user/{user_id_param}")
async def get_user_trips(user_id_param: str) -> List[Dict]:
    """Trips d'un utilisateur (rétrocompatibilité)."""
    sb = _require_supabase()
    res = sb.from_("trip_details") \
        .select("*") \
        .eq("user_id", user_id_param) \
        .order("created_at", desc=True) \
        .execute()
    return res.data or []


@router.get("/{trip_id}/saved")
async def is_trip_saved(
    trip_id: str,
    user_id: str = Depends(get_current_user_id),
) -> Dict:
    """Vérifie si un trip est sauvegardé par l'utilisateur."""
    sb = _require_supabase()
    res = sb.from_("user_saved_trips") \
        .select("id") \
        .eq("user_id", user_id) \
        .eq("trip_id", trip_id) \
        .maybeSingle() \
        .execute()
    return {"saved": res.data is not None}


@router.get("/{trip_id}")
async def get_trip(trip_id: str) -> Dict:
    """Récupère les détails d'un voyage par son ID."""
    if not _supabase_service or not _supabase_service.is_configured():
        raise HTTPException(503, detail="Supabase non configuré")

    trip = await _supabase_service.get_trip(trip_id)
    if not trip:
        raise HTTPException(404, detail="Voyage introuvable")

    return trip


@router.delete("/{trip_id}", status_code=204)
async def delete_trip(
    trip_id: str,
    user_id: str = Depends(get_current_user_id),
):
    """Supprime un trip (l'utilisateur doit en être le propriétaire)."""
    sb = _require_supabase()
    # Vérifier ownership
    res = sb.from_("trips").select("id").eq("id", trip_id).eq("user_id", user_id).maybeSingle().execute()
    if not res.data:
        raise HTTPException(404, detail="Trip introuvable ou accès refusé")
    sb.from_("trips").delete().eq("id", trip_id).execute()


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
