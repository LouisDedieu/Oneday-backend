"""
api/city_review.py — Endpoints CRUD pour cities (highlights)
Anciennement sous /review/city, maintenant sous /cities pour cohérence
"""
import logging
import asyncio
from typing import List, Optional, Dict
from fastapi import APIRouter, HTTPException, Depends, BackgroundTasks
from pydantic import BaseModel

from utils.auth import get_current_user_id
from services.supabase_service import SupabaseService
from services.geocoding_service import batch_geocode_highlights
from models.errors import ErrorCode, get_error_message

logger = logging.getLogger("bombo.api.cities_crud")
router = APIRouter(prefix="/cities", tags=["cities"])

_supabase_service: Optional[SupabaseService] = None


def set_supabase_service(service: SupabaseService):
    global _supabase_service
    _supabase_service = service


def _require_supabase():
    if not _supabase_service or not _supabase_service.is_configured():
        raise HTTPException(503, detail="Supabase non configuré")
    return _supabase_service.supabase_client


def _check_city_ownership(sb, city_id: str, user_id: str) -> None:
    try:
        res = sb.from_("cities") \
            .select("id") \
            .eq("id", city_id) \
            .eq("user_id", user_id) \
            .maybe_single() \
            .execute()
        if not res or not res.data:
            raise HTTPException(404, detail={
                "error_code": ErrorCode.CITY_NOT_FOUND,
                "message": get_error_message(ErrorCode.CITY_NOT_FOUND),
            })
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(404, detail={
            "error_code": ErrorCode.CITY_NOT_FOUND,
            "message": get_error_message(ErrorCode.CITY_NOT_FOUND),
        })


def _check_highlight_ownership(sb, highlight_id: str, user_id: str) -> None:
    """
    Vérifie que le highlight existe et appartient à une city de l'utilisateur.
    Utilise 2 requêtes séparées pour éviter les problèmes de jointure Supabase.
    """
    try:
        # 1. Récupérer le highlight et son city_id
        highlight_res = sb.from_("city_highlights") \
            .select("id, city_id") \
            .eq("id", highlight_id) \
            .maybe_single() \
            .execute()

        if not highlight_res or not highlight_res.data:
            raise HTTPException(404, detail={
                "error_code": ErrorCode.HIGHLIGHT_NOT_FOUND,
                "message": get_error_message(ErrorCode.HIGHLIGHT_NOT_FOUND),
            })

        city_id = highlight_res.data.get("city_id")
        if not city_id:
            raise HTTPException(404, detail={
                "error_code": ErrorCode.HIGHLIGHT_NOT_FOUND,
                "message": get_error_message(ErrorCode.HIGHLIGHT_NOT_FOUND),
            })

        # 2. Vérifier que la city appartient à l'utilisateur
        city_res = sb.from_("cities") \
            .select("id") \
            .eq("id", city_id) \
            .eq("user_id", user_id) \
            .maybe_single() \
            .execute()

        if not city_res or not city_res.data:
            raise HTTPException(404, detail={
                "error_code": ErrorCode.HIGHLIGHT_NOT_FOUND,
                "message": get_error_message(ErrorCode.HIGHLIGHT_NOT_FOUND),
            })

    except HTTPException:
        raise
    except Exception:
        raise HTTPException(404, detail={
            "error_code": ErrorCode.HIGHLIGHT_NOT_FOUND,
            "message": get_error_message(ErrorCode.HIGHLIGHT_NOT_FOUND),
        })


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


class CreateHighlightBody(BaseModel):
    name: str
    category: str = "other"
    subtype: Optional[str] = None
    address: Optional[str] = None
    description: Optional[str] = None
    price_range: Optional[str] = None
    tips: Optional[str] = None
    is_must_see: bool = False
    latitude: Optional[float] = None
    longitude: Optional[float] = None


# -- Routes --------------------------------------------------------------------

@router.get("/{city_id}/edit")
async def get_city_for_edit(city_id: str) -> Dict:
    """
    Retourne la city complète pour l'édition :
    city + tous les highlights (validés et non-validés).
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

    if not city_res or not city_res.data:
        raise HTTPException(404, detail="City introuvable")

    city = city_res.data
    highlights = highlights_res.data if highlights_res else []
    budget = budget_res.data if budget_res else None

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


@router.post("/{city_id}/highlights", status_code=201)
async def create_highlight(
    city_id: str,
    body: CreateHighlightBody,
    user_id: str = Depends(get_current_user_id),
) -> Dict:
    """Cree un nouveau highlight pour une city."""
    sb = _require_supabase()
    _check_city_ownership(sb, city_id, user_id)

    # Valider la categorie
    valid_categories = ['food', 'culture', 'nature', 'shopping', 'nightlife', 'other']
    if body.category not in valid_categories:
        raise HTTPException(400, detail=f"Categorie invalide. Valeurs acceptees: {valid_categories}")

    # Recuperer l'ordre max actuel
    max_order_res = sb.from_("city_highlights") \
        .select("highlight_order") \
        .eq("city_id", city_id) \
        .order("highlight_order", desc=True) \
        .limit(1) \
        .execute()

    max_order = 0
    if max_order_res.data and len(max_order_res.data) > 0:
        max_order = (max_order_res.data[0].get("highlight_order") or 0) + 1

    # Creer le highlight
    new_highlight = {
        "city_id": city_id,
        "name": body.name,
        "category": body.category,
        "subtype": body.subtype,
        "address": body.address,
        "description": body.description,
        "price_range": body.price_range,
        "tips": body.tips,
        "is_must_see": body.is_must_see,
        "highlight_order": max_order,
        "validated": True,
        "latitude": body.latitude,
        "longitude": body.longitude,
    }

    result = sb.from_("city_highlights").insert(new_highlight).execute()

    if not result.data:
        raise HTTPException(500, detail="Erreur lors de la creation du highlight")

    return result.data[0]


@router.patch("/highlights/{highlight_id}", status_code=200)
async def update_highlight(
    highlight_id: str,
    body: HighlightUpdateBody,
    user_id: str = Depends(get_current_user_id),
) -> Dict:
    """Met a jour les champs d'un highlight."""
    sb = _require_supabase()
    _check_highlight_ownership(sb, highlight_id, user_id)
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
    _check_highlight_ownership(sb, highlight_id, user_id)
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
    _check_city_ownership(sb, body.city_id, user_id)

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
    _check_highlight_ownership(sb, highlight_id, user_id)
    sb.from_("city_highlights").delete().eq("id", highlight_id).execute()


async def _geocode_city_highlights_in_background(city_id: str) -> None:
    """
    Background task to geocode all highlights for a city.
    This runs AFTER the city has been synced, so it doesn't block the UI.
    """
    if not _supabase_service or not _supabase_service.is_configured():
        logger.warning("Supabase not configured for background geocoding")
        return

    sb = _supabase_service.supabase_client

    try:
        # Recuperer les infos de la city pour le contexte de geocodage
        city_res = sb.from_("cities") \
            .select("city_name, country") \
            .eq("id", city_id) \
            .single() \
            .execute()
        city_name = city_res.data.get("city_name", "") if city_res.data else ""
        country = city_res.data.get("country") if city_res.data else None

        if not city_name:
            logger.warning(f"No city name found for city {city_id}, skipping geocoding")
            return

        # Recuperer les highlights sans coordonnees
        highlights_res = sb.from_("city_highlights") \
            .select("id, name, address, latitude, longitude") \
            .eq("city_id", city_id) \
            .eq("validated", True) \
            .execute()
        highlights = highlights_res.data or []

        # Callback pour persister les coordonnees
        async def update_highlight_coords(highlight_id: str, lat: float, lon: float):
            await asyncio.to_thread(
                lambda: sb.from_("city_highlights")
                    .update({"latitude": lat, "longitude": lon})
                    .eq("id", highlight_id)
                    .execute()
            )

        results = await batch_geocode_highlights(
            highlights=highlights,
            city_name=city_name,
            country=country,
            update_callback=update_highlight_coords,
        )

        logger.info(f"Background geocoding complete for city {city_id}: {len(results)} highlights")

    except Exception as e:
        logger.error(f"Background geocoding failed for city {city_id}: {e}")


@router.post("/{city_id}/sync", status_code=200)
async def sync_city_data(
    city_id: str,
    background_tasks: BackgroundTasks,
    user_id: str = Depends(get_current_user_id),
) -> Dict:
    """
    Synchronise la city apres validation des highlights :
    1. Supprime les highlights non-valides (validated=false)
    2. Recalcule l'ordre des highlights restants
    3. (Background) Geocode les highlights sans coordonnees

    Le geocoding se fait en arriere-plan apres la synchronisation.
    """
    sb = _require_supabase()
    _check_city_ownership(sb, city_id, user_id)

    # 1. Supprimer les highlights non-valides
    await asyncio.to_thread(
        lambda: sb.from_("city_highlights")
            .delete()
            .eq("city_id", city_id)
            .eq("validated", False)
            .execute()
    )

    # 2. Recuperer les highlights restants et recalculer l'ordre
    remaining_res = await asyncio.to_thread(
        lambda: sb.from_("city_highlights")
            .select("id, highlight_order")
            .eq("city_id", city_id)
            .order("highlight_order")
            .execute()
    )

    remaining = remaining_res.data or []
    for i, h in enumerate(remaining):
        await asyncio.to_thread(
            lambda hid=h["id"], order=i: sb.from_("city_highlights")
                .update({"highlight_order": order})
                .eq("id", hid)
                .execute()
        )

    # 3. Lancer le geocoding en arriere-plan (non-bloquant)
    background_tasks.add_task(_geocode_city_highlights_in_background, city_id)

    return {
        "synced": True,
        "remaining_highlights": len(remaining),
        "geocoding_scheduled": True,
    }
