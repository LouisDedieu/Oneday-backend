"""
api/notifications.py — Gestion des notifications push et in-app
"""
import logging
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from datetime import datetime

from utils.auth import get_current_user_id
from services.supabase_service import SupabaseService

logger = logging.getLogger("bombo.api.notifications")
router = APIRouter(prefix="/notifications", tags=["notifications"])

_supabase_service: Optional[SupabaseService] = None


def set_supabase_service(service: SupabaseService):
    global _supabase_service
    _supabase_service = service


# ── Modèles ───────────────────────────────────────────────────────────────────


class PushTokenRequest(BaseModel):
    expo_push_token: str
    device_type: str  # 'ios' | 'android'


class NotificationPreferences(BaseModel):
    push_enabled: bool = True
    analysis_complete_push: bool = True
    analysis_error_push: bool = True
    content_saved_push: bool = False


class NotificationPreferencesUpdate(BaseModel):
    push_enabled: Optional[bool] = None
    analysis_complete_push: Optional[bool] = None
    analysis_error_push: Optional[bool] = None
    content_saved_push: Optional[bool] = None


class Notification(BaseModel):
    id: str
    type: str
    title: str
    body: str
    data: dict
    read_at: Optional[str]
    created_at: str


class NotificationListResponse(BaseModel):
    notifications: List[Notification]
    unread_count: int
    total_count: int
    has_more: bool


class UnreadCountResponse(BaseModel):
    unread_count: int


# ── Routes Push Token ─────────────────────────────────────────────────────────


@router.post("/push-token")
async def register_push_token(
    request: PushTokenRequest,
    user_id: str = Depends(get_current_user_id),
):
    """
    Enregistre ou met à jour un token push pour l'utilisateur.
    Si le token existe déjà, le réactive.
    """
    if not _supabase_service or not _supabase_service.supabase_client:
        raise HTTPException(503, detail="Supabase non configuré")

    sb = _supabase_service.supabase_client

    try:
        # Vérifier si le token existe déjà
        existing = sb.from_("push_tokens") \
            .select("id, is_active") \
            .eq("user_id", user_id) \
            .eq("expo_push_token", request.expo_push_token) \
            .maybe_single() \
            .execute()

        if existing.data:
            # Réactiver si inactif
            if not existing.data["is_active"]:
                sb.from_("push_tokens") \
                    .update({"is_active": True}) \
                    .eq("id", existing.data["id"]) \
                    .execute()
            return {"status": "updated", "token_id": existing.data["id"]}

        # Créer un nouveau token
        result = sb.from_("push_tokens") \
            .insert({
                "user_id": user_id,
                "expo_push_token": request.expo_push_token,
                "device_type": request.device_type,
                "is_active": True,
            }) \
            .execute()

        token_id = result.data[0]["id"] if result.data else None
        return {"status": "created", "token_id": token_id}

    except Exception as e:
        logger.error(f"Erreur lors de l'enregistrement du token push: {e}")
        raise HTTPException(500, detail="Erreur lors de l'enregistrement du token")


@router.delete("/push-token")
async def deactivate_push_token(
    request: PushTokenRequest,
    user_id: str = Depends(get_current_user_id),
):
    """
    Désactive un token push (généralement appelé lors du logout).
    """
    if not _supabase_service or not _supabase_service.supabase_client:
        raise HTTPException(503, detail="Supabase non configuré")

    sb = _supabase_service.supabase_client

    try:
        sb.from_("push_tokens") \
            .update({"is_active": False}) \
            .eq("user_id", user_id) \
            .eq("expo_push_token", request.expo_push_token) \
            .execute()

        return {"status": "deactivated"}

    except Exception as e:
        logger.error(f"Erreur lors de la désactivation du token push: {e}")
        raise HTTPException(500, detail="Erreur lors de la désactivation du token")


# ── Routes Préférences ────────────────────────────────────────────────────────


@router.get("/preferences", response_model=NotificationPreferences)
async def get_notification_preferences(
    user_id: str = Depends(get_current_user_id),
) -> NotificationPreferences:
    """
    Récupère les préférences de notification de l'utilisateur.
    """
    if not _supabase_service or not _supabase_service.supabase_client:
        raise HTTPException(503, detail="Supabase non configuré")

    sb = _supabase_service.supabase_client

    try:
        result = sb.from_("notification_preferences") \
            .select("*") \
            .eq("user_id", user_id) \
            .maybe_single() \
            .execute()

        if result.data:
            return NotificationPreferences(
                push_enabled=result.data.get("push_enabled", True),
                analysis_complete_push=result.data.get("analysis_complete_push", True),
                analysis_error_push=result.data.get("analysis_error_push", True),
                content_saved_push=result.data.get("content_saved_push", False),
            )

        # Créer les préférences par défaut si elles n'existent pas
        default_prefs = {
            "user_id": user_id,
            "push_enabled": True,
            "analysis_complete_push": True,
            "analysis_error_push": True,
            "content_saved_push": False,
        }
        sb.from_("notification_preferences").insert(default_prefs).execute()

        return NotificationPreferences(**default_prefs)

    except Exception as e:
        logger.error(f"Erreur lors de la récupération des préférences: {e}")
        raise HTTPException(500, detail="Erreur lors de la récupération des préférences")


@router.patch("/preferences", response_model=NotificationPreferences)
async def update_notification_preferences(
    updates: NotificationPreferencesUpdate,
    user_id: str = Depends(get_current_user_id),
) -> NotificationPreferences:
    """
    Met à jour les préférences de notification de l'utilisateur.
    """
    if not _supabase_service or not _supabase_service.supabase_client:
        raise HTTPException(503, detail="Supabase non configuré")

    sb = _supabase_service.supabase_client

    try:
        # Construire le payload de mise à jour (uniquement les champs non-None)
        update_data = {k: v for k, v in updates.model_dump().items() if v is not None}

        if not update_data:
            # Rien à mettre à jour, retourner les préférences actuelles
            return await get_notification_preferences(user_id)

        # Upsert pour créer ou mettre à jour
        result = sb.from_("notification_preferences") \
            .upsert({
                "user_id": user_id,
                **update_data,
            }, on_conflict="user_id") \
            .execute()

        if result.data:
            prefs = result.data[0]
            return NotificationPreferences(
                push_enabled=prefs.get("push_enabled", True),
                analysis_complete_push=prefs.get("analysis_complete_push", True),
                analysis_error_push=prefs.get("analysis_error_push", True),
                content_saved_push=prefs.get("content_saved_push", False),
            )

        # Fallback: récupérer les préférences
        return await get_notification_preferences(user_id)

    except Exception as e:
        logger.error(f"Erreur lors de la mise à jour des préférences: {e}")
        raise HTTPException(500, detail="Erreur lors de la mise à jour des préférences")


# ── Routes Notifications ──────────────────────────────────────────────────────


@router.get("", response_model=NotificationListResponse)
async def get_notifications(
    limit: int = 20,
    offset: int = 0,
    user_id: str = Depends(get_current_user_id),
) -> NotificationListResponse:
    """
    Récupère la liste paginée des notifications de l'utilisateur.
    """
    if not _supabase_service or not _supabase_service.supabase_client:
        raise HTTPException(503, detail="Supabase non configuré")

    sb = _supabase_service.supabase_client

    try:
        # Récupérer les notifications paginées
        notifications_res = sb.from_("notifications") \
            .select("*") \
            .eq("user_id", user_id) \
            .order("created_at", desc=True) \
            .range(offset, offset + limit - 1) \
            .execute()

        notifications = notifications_res.data or []

        # Compter le total et les non-lues
        count_res = sb.from_("notifications") \
            .select("id, read_at") \
            .eq("user_id", user_id) \
            .execute()

        all_notifs = count_res.data or []
        total_count = len(all_notifs)
        unread_count = sum(1 for n in all_notifs if n.get("read_at") is None)

        return NotificationListResponse(
            notifications=[
                Notification(
                    id=n["id"],
                    type=n["type"],
                    title=n["title"],
                    body=n["body"],
                    data=n.get("data") or {},
                    read_at=n.get("read_at"),
                    created_at=n["created_at"],
                )
                for n in notifications
            ],
            unread_count=unread_count,
            total_count=total_count,
            has_more=offset + limit < total_count,
        )

    except Exception as e:
        logger.error(f"Erreur lors de la récupération des notifications: {e}")
        raise HTTPException(500, detail="Erreur lors de la récupération des notifications")


@router.get("/unread-count", response_model=UnreadCountResponse)
async def get_unread_count(
    user_id: str = Depends(get_current_user_id),
) -> UnreadCountResponse:
    """
    Récupère le nombre de notifications non lues.
    """
    if not _supabase_service or not _supabase_service.supabase_client:
        raise HTTPException(503, detail="Supabase non configuré")

    sb = _supabase_service.supabase_client

    try:
        result = sb.from_("notifications") \
            .select("id", count="exact") \
            .eq("user_id", user_id) \
            .is_("read_at", "null") \
            .execute()

        unread_count = result.count if result.count is not None else 0
        return UnreadCountResponse(unread_count=unread_count)

    except Exception as e:
        logger.error(f"Erreur lors du comptage des notifications non lues: {e}")
        raise HTTPException(500, detail="Erreur lors du comptage des notifications")


@router.post("/{notification_id}/read")
async def mark_notification_as_read(
    notification_id: str,
    user_id: str = Depends(get_current_user_id),
):
    """
    Marque une notification comme lue.
    """
    if not _supabase_service or not _supabase_service.supabase_client:
        raise HTTPException(503, detail="Supabase non configuré")

    sb = _supabase_service.supabase_client

    try:
        result = sb.from_("notifications") \
            .update({"read_at": datetime.utcnow().isoformat()}) \
            .eq("id", notification_id) \
            .eq("user_id", user_id) \
            .execute()

        if not result.data:
            raise HTTPException(404, detail="Notification non trouvée")

        return {"status": "read"}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Erreur lors du marquage de la notification comme lue: {e}")
        raise HTTPException(500, detail="Erreur lors du marquage de la notification")


@router.post("/read-all")
async def mark_all_notifications_as_read(
    user_id: str = Depends(get_current_user_id),
):
    """
    Marque toutes les notifications non lues comme lues.
    """
    if not _supabase_service or not _supabase_service.supabase_client:
        raise HTTPException(503, detail="Supabase non configuré")

    sb = _supabase_service.supabase_client

    try:
        sb.from_("notifications") \
            .update({"read_at": datetime.utcnow().isoformat()}) \
            .eq("user_id", user_id) \
            .is_("read_at", "null") \
            .execute()

        return {"status": "all_read"}

    except Exception as e:
        logger.error(f"Erreur lors du marquage de toutes les notifications comme lues: {e}")
        raise HTTPException(500, detail="Erreur lors du marquage des notifications")
