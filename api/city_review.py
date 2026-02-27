"""
api/city_review.py — Endpoints du mode review pour les cities
"""
import logging
import asyncio
from typing import List, Optional, Dict
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel

from utils.auth import get_current_user_id
from services.supabase_service import SupabaseService

logger = logging.getLogger("bombo.api.city_review")
router = APIRouter(prefix="/review/city", tags=["city_review"])

_supabase_service: Optional[SupabaseService] = None


def set_supabase_service(service: SupabaseService):
    global _supabase_service
    _supabase_service = service


def _require_supabase():
    if not _supabase_service or not _supabase_service.is_configured():
        raise HTTPException(503, detail="Supabase non configure")
    return _supabase_service.supabase_client


# -- Modeles -------------------------------------------------------------------

class HighlightUpdateBody(BaseModel):
    name: Optional[str] = None
    category: Optional[str] = None
    subtype: Optional[str] = None
    address: Optional[str] = None
    description: Optional[str] = None
    price_range: Optional[str] = None
    tips: Optional[str] = None
    is_must_see: Optional[bool] = None
    validated: Optional[bool] = None


class CoordinatesBody(BaseModel):
    lat: float
    lon: float


class ReorderHighlightsBody(BaseModel):
    city_id: str
    highlights: List[Dict]  # [{id: str, order: int}]


# -- Routes --------------------------------------------------------------------

@router.get("/{city_id}")
async def get_city_for_review(city_id: str) -> Dict:
    """
    Retourne la city complete pour le mode review :
    city + tous les highlights (valides et non-valides).
    """
    sb = _require_supabase()

    def fetch_city():
        return sb.from_("cities") \
            .select("id, city_title, city_name, country, vibe_tags, source_url, content_creator_handle") \
            .eq("id", city_id) \
            .single() \
            .execute()

    def fetch_highlights():
        return sb.from_("city_highlights") \
            .select("*") \
            .eq("city_id", city_id) \
            .order("highlight_order") \
            .execute()

    def fetch_budget():
        return sb.from_("city_budgets") \
            .select("*") \
            .eq("city_id", city_id) \
            .maybe_single() \
            .execute()

    city_res, highlights_res, budget_res = await asyncio.gather(
        asyncio.to_thread(fetch_city),
        asyncio.to_thread(fetch_highlights),
        asyncio.to_thread(fetch_budget),
    )

    if not city_res.data:
        raise HTTPException(404, detail="City introuvable")

    city = city_res.data
    highlights = highlights_res.data or []
    budget = budget_res.data

    # Formater les highlights
    formatted_highlights = [
        {
            "id": h["id"],
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
            "highlight_order": h.get("highlight_order", 0),
            "validated": h.get("validated", True),
        }
        for h in highlights
    ]

    # Compter par categorie
    category_counts = {}
    for h in formatted_highlights:
        cat = h["category"]
        category_counts[cat] = category_counts.get(cat, 0) + 1

    return {
        **city,
        "highlights": formatted_highlights,
        "highlights_count": len(formatted_highlights),
        "category_counts": category_counts,
        "budget": budget,
    }


@router.patch("/highlights/{highlight_id}", status_code=200)
async def update_highlight(
    highlight_id: str,
    body: HighlightUpdateBody,
    user_id: str = Depends(get_current_user_id),
) -> Dict:
    """Met a jour les champs d'un highlight."""
    sb = _require_supabase()
    payload = {k: v for k, v in body.model_dump().items() if v is not None}
    if not payload:
        return {"updated": False}

    # Valider la categorie si presente
    valid_categories = ['food', 'culture', 'nature', 'shopping', 'nightlife', 'other']
    if 'category' in payload and payload['category'] not in valid_categories:
        raise HTTPException(400, detail=f"Categorie invalide. Valeurs acceptees: {valid_categories}")

    sb.from_("city_highlights").update(payload).eq("id", highlight_id).execute()
    return {"updated": True}


@router.patch("/highlights/{highlight_id}/coordinates", status_code=200)
async def update_highlight_coordinates(
    highlight_id: str,
    body: CoordinatesBody,
    user_id: str = Depends(get_current_user_id),
) -> Dict:
    """Met a jour latitude/longitude d'un highlight."""
    sb = _require_supabase()
    sb.from_("city_highlights") \
        .update({"latitude": body.lat, "longitude": body.lon}) \
        .eq("id", highlight_id) \
        .execute()
    return {"updated": True}


@router.patch("/highlights/reorder", status_code=200)
async def reorder_highlights(
    body: ReorderHighlightsBody,
    user_id: str = Depends(get_current_user_id),
) -> Dict:
    """Reordonne les highlights d'une city (pour drag & drop)."""
    sb = _require_supabase()

    for item in body.highlights:
        highlight_id = item.get("id")
        order = item.get("order")
        if highlight_id and order is not None:
            sb.from_("city_highlights") \
                .update({"highlight_order": order}) \
                .eq("id", highlight_id) \
                .execute()

    return {"reordered": True, "count": len(body.highlights)}


@router.delete("/highlights/{highlight_id}", status_code=204)
async def delete_highlight(
    highlight_id: str,
    user_id: str = Depends(get_current_user_id),
):
    """Supprime un highlight."""
    sb = _require_supabase()
    sb.from_("city_highlights").delete().eq("id", highlight_id).execute()


@router.post("/{city_id}/sync", status_code=200)
async def sync_city_data(
    city_id: str,
    user_id: str = Depends(get_current_user_id),
) -> Dict:
    """
    Synchronise la city apres validation des highlights :
    - Supprime les highlights non-valides (validated=false)
    - Recalcule l'ordre des highlights restants
    """
    sb = _require_supabase()

    # 1. Supprimer les highlights non-valides
    sb.from_("city_highlights") \
        .delete() \
        .eq("city_id", city_id) \
        .eq("validated", False) \
        .execute()

    # 2. Recuperer les highlights restants et recalculer l'ordre
    remaining_res = sb.from_("city_highlights") \
        .select("id, highlight_order") \
        .eq("city_id", city_id) \
        .order("highlight_order") \
        .execute()

    remaining = remaining_res.data or []
    for i, h in enumerate(remaining):
        sb.from_("city_highlights") \
            .update({"highlight_order": i}) \
            .eq("id", h["id"]) \
            .execute()

    return {"synced": True, "remaining_highlights": len(remaining)}
