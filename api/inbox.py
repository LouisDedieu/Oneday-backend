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
    cityId: Optional[str]
    entityType: str  # 'trip' | 'city'
    title: str
    sourceUrl: str
    platform: str
    createdAt: str
    status: str
    progressPct: int
    errorMessage: Optional[str]
    isLocal: bool = False
    highlightsCount: Optional[int] = None


# ── Route ─────────────────────────────────────────────────────────────────────

@router.get("", response_model=List[InboxJob])
async def get_inbox(user_id: str = Depends(get_current_user_id)) -> List[InboxJob]:
    """
    Retourne la liste des jobs d'analyse de l'utilisateur.
    Exclut les trips/cities terminés et déjà sauvegardés (= validés).
    """
    if not _supabase_service or not _supabase_service.supabase_client:
        raise HTTPException(503, detail="Supabase non configuré")

    sb = _supabase_service.supabase_client

    # 1. Jobs de l'utilisateur (with entity_type)
    jobs_res = sb.from_("analysis_jobs") \
        .select("id, source_url, status, progress_percentage, error_message, created_at, entity_type, city_id") \
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

    # 3. Cities liés à ces jobs
    city_ids_from_jobs = [j["city_id"] for j in jobs if j.get("city_id")]
    cities_res = sb.from_("cities") \
        .select("id, city_title") \
        .in_("id", city_ids_from_jobs) \
        .execute() if city_ids_from_jobs else None

    cities = cities_res.data if cities_res else []
    city_by_id = {c["id"]: c for c in cities}

    # 4. Get highlights count for cities
    highlights_counts = {}
    if city_ids_from_jobs:
        for city_id in city_ids_from_jobs:
            count_res = sb.from_("city_highlights") \
                .select("id", count="exact") \
                .eq("city_id", city_id) \
                .execute()
            highlights_counts[city_id] = count_res.count or 0

    # 5. Trips déjà sauvegardés
    saved_trip_ids: set = set()
    if trip_ids:
        saved_res = sb.from_("user_saved_trips") \
            .select("trip_id") \
            .eq("user_id", user_id) \
            .in_("trip_id", trip_ids) \
            .execute()
        saved_trip_ids = {s["trip_id"] for s in (saved_res.data or [])}

    # 6. Cities déjà sauvegardées
    saved_city_ids: set = set()
    if city_ids_from_jobs:
        saved_city_res = sb.from_("user_saved_cities") \
            .select("city_id") \
            .eq("user_id", user_id) \
            .in_("city_id", city_ids_from_jobs) \
            .execute()
        saved_city_ids = {s["city_id"] for s in (saved_city_res.data or [])}

    # 7. Construire la réponse
    def detect_platform(url: str) -> str:
        if "tiktok.com" in url.lower():
            return "tiktok"
        if "instagram.com" in url.lower():
            return "instagram"
        return "unknown"

    result = []
    for job in jobs:
        entity_type = job.get("entity_type") or "trip"
        city_id = job.get("city_id")
        trip = trip_by_job_id.get(job["id"])
        city = city_by_id.get(city_id) if city_id else None

        # Exclure les trips/cities terminés ET déjà sauvegardés
        if job["status"] == "done":
            if entity_type == "trip" and trip and trip["id"] in saved_trip_ids:
                continue
            if entity_type == "city" and city_id and city_id in saved_city_ids:
                continue

        # Determine title based on entity type
        if entity_type == "city" and city:
            title = city["city_title"]
        elif trip:
            title = trip["trip_title"]
        else:
            title = "Analyse en cours…"

        result.append(InboxJob(
            jobId=job["id"],
            tripId=trip["id"] if trip else None,
            cityId=city_id,
            entityType=entity_type,
            title=title,
            sourceUrl=job["source_url"] or "",
            platform=detect_platform(job["source_url"] or ""),
            createdAt=job["created_at"],
            status=job["status"] or "pending",
            progressPct=job["progress_percentage"] or 0,
            errorMessage=job["error_message"],
            highlightsCount=highlights_counts.get(city_id) if city_id else None,
        ))

    return result


@router.delete("/{job_id}")
async def delete_inbox_job(job_id: str, user_id: str = Depends(get_current_user_id)):
    """
    Supprime un job d'analyse et toutes les données associées (trip ou city).
    """
    if not _supabase_service or not _supabase_service.supabase_client:
        raise HTTPException(503, detail="Supabase non configuré")

    sb = _supabase_service.supabase_client

    # 1. Vérifier que le job existe et appartient à l'utilisateur
    job_res = sb.from_("analysis_jobs") \
        .select("id, entity_type, city_id") \
        .eq("id", job_id) \
        .eq("user_id", user_id) \
        .maybe_single() \
        .execute()

    if not job_res.data:
        raise HTTPException(404, detail="Job non trouvé")

    job = job_res.data
    entity_type = job.get("entity_type") or "trip"
    city_id = job.get("city_id")

    # 2. Supprimer l'entité associée (cascade automatique pour les sous-tables)
    if entity_type == "city" and city_id:
        # Supprimer les références user_saved_cities
        sb.from_("user_saved_cities") \
            .delete() \
            .eq("city_id", city_id) \
            .execute()

        # Nullifier city_id dans analysis_jobs pour éviter la contrainte FK
        sb.from_("analysis_jobs") \
            .update({"city_id": None}) \
            .eq("id", job_id) \
            .execute()

        # Supprimer la city (cascade: city_highlights, city_budgets, city_practical_info)
        sb.from_("cities") \
            .delete() \
            .eq("id", city_id) \
            .execute()
    else:
        # Chercher le trip lié à ce job
        trip_res = sb.from_("trips") \
            .select("id") \
            .eq("job_id", job_id) \
            .maybe_single() \
            .execute()

        if trip_res.data:
            trip_id = trip_res.data["id"]

            # Supprimer les références user_saved_trips
            sb.from_("user_saved_trips") \
                .delete() \
                .eq("trip_id", trip_id) \
                .execute()

            # Supprimer le trip (cascade: destinations, itinerary_days, spots, logistics, budgets, practical_info)
            sb.from_("trips") \
                .delete() \
                .eq("id", trip_id) \
                .execute()

    # 3. Supprimer le job d'analyse
    sb.from_("analysis_jobs") \
        .delete() \
        .eq("id", job_id) \
        .execute()

    return {"deleted": True}
