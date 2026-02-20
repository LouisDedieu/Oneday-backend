"""
api/inbox.py — Liste des jobs d'analyse (Inbox)
GET /inbox → analysis_jobs + trips associés + filtre user_saved_trips
"""
import logging
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from utils.auth import get_current_user_id
from services.supabase_service import SupabaseService

logger = logging.getLogger("bombo.api.inbox")
router = APIRouter(prefix="/inbox", tags=["inbox"])

_supabase_service: Optional[SupabaseService] = None


def set_supabase_service(service: SupabaseService):
    global _supabase_service
    _supabase_service = service


# ── Modèles ───────────────────────────────────────────────────────────────────

class InboxJob(BaseModel):
    jobId: str
    tripId: Optional[str]
    title: str
    sourceUrl: str
    platform: str
    createdAt: str
    status: str
    progressPct: int
    errorMessage: Optional[str]
    isLocal: bool = False


# ── Route ─────────────────────────────────────────────────────────────────────

@router.get("", response_model=List[InboxJob])
async def get_inbox(user_id: str = Depends(get_current_user_id)) -> List[InboxJob]:
    """
    Retourne la liste des jobs d'analyse de l'utilisateur.
    Exclut les trips terminés et déjà sauvegardés (= validés).
    """
    if not _supabase_service or not _supabase_service.supabase_client:
        raise HTTPException(503, detail="Supabase non configuré")

    sb = _supabase_service.supabase_client

    # 1. Jobs de l'utilisateur
    jobs_res = sb.from_("analysis_jobs") \
        .select("id, source_url, status, progress_percentage, error_message, created_at") \
        .eq("user_id", user_id) \
        .order("created_at", desc=True) \
        .execute()

    jobs = jobs_res.data or []
    if not jobs:
        return []

    job_ids = [j["id"] for j in jobs]

    # 2. Trips liés à ces jobs
    trips_res = sb.from_("trips") \
        .select("id, job_id, trip_title") \
        .in_("job_id", job_ids) \
        .execute()

    trips = trips_res.data or []
    trip_by_job_id = {t["job_id"]: t for t in trips}
    trip_ids = [t["id"] for t in trips]

    # 3. Trips déjà sauvegardés
    saved_trip_ids: set = set()
    if trip_ids:
        saved_res = sb.from_("user_saved_trips") \
            .select("trip_id") \
            .eq("user_id", user_id) \
            .in_("trip_id", trip_ids) \
            .execute()
        saved_trip_ids = {s["trip_id"] for s in (saved_res.data or [])}

    # 4. Construire la réponse — même filtre que le frontend
    def detect_platform(url: str) -> str:
        if "tiktok.com" in url.lower():
            return "tiktok"
        if "instagram.com" in url.lower():
            return "instagram"
        return "unknown"

    result = []
    for job in jobs:
        trip = trip_by_job_id.get(job["id"])
        # Exclure les trips terminés ET déjà sauvegardés
        if job["status"] == "done" and trip and trip["id"] in saved_trip_ids:
            continue

        result.append(InboxJob(
            jobId=job["id"],
            tripId=trip["id"] if trip else None,
            title=trip["trip_title"] if trip else "Analyse en cours…",
            sourceUrl=job["source_url"] or "",
            platform=detect_platform(job["source_url"] or ""),
            createdAt=job["created_at"],
            status=job["status"] or "pending",
            progressPct=job["progress_percentage"] or 0,
            errorMessage=job["error_message"],
        ))

    return result
