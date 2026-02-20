"""
api/profile.py — Profil utilisateur et statistiques
GET /profile → { profile, stats }
"""
import logging
import asyncio
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from utils.auth import get_current_user_id
from services.supabase_service import SupabaseService

logger = logging.getLogger("bombo.api.profile")
router = APIRouter(prefix="/profile", tags=["profile"])

_supabase_service: Optional[SupabaseService] = None


def set_supabase_service(service: SupabaseService):
    global _supabase_service
    _supabase_service = service


# ── Modèles ───────────────────────────────────────────────────────────────────

class ProfileData(BaseModel):
    username: Optional[str] = None
    full_name: Optional[str] = None
    bio: Optional[str] = None
    avatar_url: Optional[str] = None
    created_at: Optional[str] = None


class Stats(BaseModel):
    tripsCreated: int = 0
    tripsSaved: int = 0
    totalViews: int = 0
    videosAnalyzed: int = 0


class ProfileResponse(BaseModel):
    profile: Optional[ProfileData] = None
    stats: Stats


# ── Route ─────────────────────────────────────────────────────────────────────

@router.get("", response_model=ProfileResponse)
async def get_profile(user_id: str = Depends(get_current_user_id)) -> ProfileResponse:
    """Retourne le profil et les statistiques de l'utilisateur connecté."""
    if not _supabase_service or not _supabase_service.supabase_client:
        raise HTTPException(503, detail="Supabase non configuré")

    sb = _supabase_service.supabase_client

    # 4 requêtes en parallèle
    def fetch_profile():
        return sb.from_("profiles") \
            .select("username, full_name, bio, avatar_url, created_at") \
            .eq("id", user_id) \
            .maybeSingle() \
            .execute()

    def fetch_trips():
        return sb.from_("trips") \
            .select("id, views_count") \
            .eq("user_id", user_id) \
            .execute()

    def fetch_saved_count():
        return sb.from_("user_saved_trips") \
            .select("id", count="exact", head=True) \
            .eq("user_id", user_id) \
            .execute()

    def fetch_jobs_count():
        return sb.from_("analysis_jobs") \
            .select("id", count="exact", head=True) \
            .eq("user_id", user_id) \
            .eq("status", "completed") \
            .execute()

    profile_res, trips_res, saved_res, jobs_res = await asyncio.gather(
        asyncio.to_thread(fetch_profile),
        asyncio.to_thread(fetch_trips),
        asyncio.to_thread(fetch_saved_count),
        asyncio.to_thread(fetch_jobs_count),
    )

    profile_data = None
    if profile_res.data:
        profile_data = ProfileData(**profile_res.data)

    trips = trips_res.data or []
    stats = Stats(
        tripsCreated=len(trips),
        totalViews=sum(t.get("views_count") or 0 for t in trips),
        tripsSaved=saved_res.count or 0,
        videosAnalyzed=jobs_res.count or 0,
    )

    return ProfileResponse(profile=profile_data, stats=stats)
