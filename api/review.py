"""
api/review.py — Endpoints du mode review (validation d'itinéraire)
"""
import logging
import asyncio
from typing import List, Optional, Dict, Any
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel

from utils.auth import get_current_user_id
from services.supabase_service import SupabaseService

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
    res = sb.from_("itinerary_days") \
        .update({"validated": body.validated}) \
        .eq("id", day_id) \
        .execute()
    return {"updated": True}


@router.post("/{trip_id}/sync", status_code=200)
async def sync_destinations(
    trip_id: str,
    user_id: str = Depends(get_current_user_id),
) -> Dict:
    """
    Synchronise les destinations après validation des jours :
    1. Supprime spots des jours non-validés
    2. Supprime jours non-validés
    3. Supprime destinations orphelines
    4. Met à jour days_spent
    5. Recalcule visit_order séquentiel
    """
    sb = _require_supabase()

    # 1. Charger tous les jours du trip
    days_res = sb.from_("itinerary_days") \
        .select("id, destination_id, validated") \
        .eq("trip_id", trip_id) \
        .execute()

    all_days = days_res.data or []
    if not all_days:
        return {"synced": True}

    invalid_days = [d for d in all_days if not d.get("validated", True)]
    valid_days   = [d for d in all_days if  d.get("validated", True)]

    # 2. Supprimer spots + jours non-validés
    if invalid_days:
        invalid_ids = [d["id"] for d in invalid_days]
        sb.from_("spots").delete().in_("itinerary_day_id", invalid_ids).execute()
        sb.from_("itinerary_days").delete().in_("id", invalid_ids).execute()

    # 3. Destinations encore référencées
    active_dest_ids = {d["destination_id"] for d in valid_days if d.get("destination_id")}

    # 4. Supprimer destinations orphelines
    dests_res = sb.from_("destinations").select("id").eq("trip_id", trip_id).execute()
    all_dest_ids = [d["id"] for d in (dests_res.data or [])]
    orphan_ids = [did for did in all_dest_ids if did not in active_dest_ids]

    if orphan_ids:
        sb.from_("destinations").delete().in_("id", orphan_ids).execute()

    # 5. Mettre à jour days_spent
    days_by_dest: Dict[str, int] = {}
    for day in valid_days:
        dest_id = day.get("destination_id")
        if dest_id:
            days_by_dest[dest_id] = days_by_dest.get(dest_id, 0) + 1

    for dest_id, count in days_by_dest.items():
        sb.from_("destinations").update({"days_spent": count}).eq("id", dest_id).execute()

    # 6. Recalculer visit_order
    remaining_res = sb.from_("destinations") \
        .select("id, visit_order") \
        .eq("trip_id", trip_id) \
        .order("visit_order") \
        .execute()

    remaining = remaining_res.data or []
    for i, dest in enumerate(remaining):
        sb.from_("destinations").update({"visit_order": i + 1}).eq("id", dest["id"]).execute()

    return {"synced": True}


@router.patch("/spots/{spot_id}", status_code=200)
async def update_spot(
    spot_id: str,
    body: SpotUpdateBody,
    user_id: str = Depends(get_current_user_id),
) -> Dict:
    """Met à jour les champs d'un spot."""
    sb = _require_supabase()
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
    sb.from_("spots").delete().eq("id", spot_id).execute()


@router.patch("/spots/{spot_id}/coordinates", status_code=200)
async def update_spot_coordinates(
    spot_id: str,
    body: CoordinatesBody,
    user_id: str = Depends(get_current_user_id),
) -> Dict:
    """Met à jour latitude/longitude d'un spot."""
    sb = _require_supabase()
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
    sb.from_("destinations") \
        .update({"latitude": body.lat, "longitude": body.lon}) \
        .eq("id", dest_id) \
        .execute()
    return {"updated": True}
