"""
Service de notifications push et in-app
Gère l'envoi de notifications via Expo Push API et le stockage dans Supabase
"""
import logging
import httpx
from typing import Optional, Dict, List, Any
from enum import Enum

from models.errors import ErrorCode, get_error_message

logger = logging.getLogger("bombo.notification_service")

EXPO_PUSH_API_URL = "https://exp.host/--/api/v2/push/send"


class NotificationType(str, Enum):
    ANALYSIS_COMPLETE = "analysis_complete"
    ANALYSIS_ERROR = "analysis_error"
    CONTENT_SAVED = "content_saved"


class NotificationService:
    """Service de gestion des notifications push et in-app"""

    def __init__(self, supabase_service):
        self.supabase = supabase_service

    async def notify_analysis_complete(
        self,
        user_id: str,
        entity_type: str,
        entity_id: str,
        title: str,
        source_url: Optional[str] = None,
    ) -> None:
        """
        Envoie une notification quand une analyse est terminée.

        Args:
            user_id: ID de l'utilisateur
            entity_type: 'trip' ou 'city'
            entity_id: ID du trip ou de la city créé(e)
            title: Titre du trip ou de la city
            source_url: URL source de la vidéo
        """
        notification_title = "Analyse terminée ✓"
        notification_body = f"Votre {entity_type} \"{title}\" est prêt à être consulté"

        data = {
            "type": NotificationType.ANALYSIS_COMPLETE.value,
            "entity_type": entity_type,
            "entity_id": entity_id,
            "title": title,
            "source_url": source_url,
        }

        await self._send_notification(
            user_id=user_id,
            notification_type=NotificationType.ANALYSIS_COMPLETE,
            title=notification_title,
            body=notification_body,
            data=data,
            preference_key="analysis_complete_push",
        )

    async def notify_analysis_error(
        self,
        user_id: str,
        job_id: str,
        error_code: str,
        source_url: Optional[str] = None,
        error_message: Optional[str] = None,
    ) -> None:
        """
        Envoie une notification quand une analyse échoue.

        Args:
            user_id: ID de l'utilisateur
            job_id: ID du job qui a échoué
            error_code: Code d'erreur (private_video, ip_blocked, unsupported_url, etc.)
            source_url: URL source de la vidéo
            error_message: Message d'erreur détaillé
        """
        notification_title = "Erreur d'analyse"

        # Messages d'erreur personnalisés selon le code
        error_messages = {
            "private_video": "La vidéo est privée ou n'est plus disponible",
            "ip_blocked": "Accès temporairement bloqué, réessayez plus tard",
            "unsupported_url": "Cette URL n'est pas supportée",
            "download_error": "Impossible de télécharger la vidéo",
            "inference_error": "Erreur lors de l'analyse de la vidéo",
        }

        notification_body = error_messages.get(
            error_code,
            error_message or "Une erreur est survenue lors de l'analyse"
        )

        data = {
            "type": NotificationType.ANALYSIS_ERROR.value,
            "job_id": job_id,
            "error_code": error_code,
            "source_url": source_url,
            "error_message": error_message,
        }

        await self._send_notification(
            user_id=user_id,
            notification_type=NotificationType.ANALYSIS_ERROR,
            title=notification_title,
            body=notification_body,
            data=data,
            preference_key="analysis_error_push",
        )

    async def _send_notification(
        self,
        user_id: str,
        notification_type: NotificationType,
        title: str,
        body: str,
        data: Dict[str, Any],
        preference_key: str,
    ) -> None:
        """
        Envoie une notification push et sauvegarde dans l'historique.

        Args:
            user_id: ID de l'utilisateur
            notification_type: Type de notification
            title: Titre de la notification
            body: Corps de la notification
            data: Données additionnelles
            preference_key: Clé de préférence pour vérifier si l'utilisateur veut ce type de notification
        """
        if not self.supabase.is_configured():
            logger.warning("Supabase non configuré, notification ignorée")
            return

        try:
            # 1. Sauvegarder dans l'historique in-app
            await self._save_notification_to_history(
                user_id=user_id,
                notification_type=notification_type,
                title=title,
                body=body,
                data=data,
            )

            # 2. Vérifier les préférences de l'utilisateur
            preferences = await self._get_user_preferences(user_id)

            if not preferences.get("push_enabled", True):
                logger.info(f"Push désactivé pour l'utilisateur {user_id}")
                return

            if not preferences.get(preference_key, True):
                logger.info(f"Notification {notification_type.value} désactivée pour l'utilisateur {user_id}")
                return

            # 3. Récupérer les tokens push actifs
            tokens = await self._get_active_push_tokens(user_id)

            if not tokens:
                logger.info(f"Aucun token push actif pour l'utilisateur {user_id}")
                return

            # 4. Envoyer les notifications push
            await self._send_expo_push_notifications(
                tokens=tokens,
                title=title,
                body=body,
                data=data,
            )

        except Exception as e:
            logger.error(f"Erreur lors de l'envoi de la notification: {e}")

    async def _save_notification_to_history(
        self,
        user_id: str,
        notification_type: NotificationType,
        title: str,
        body: str,
        data: Dict[str, Any],
    ) -> None:
        """Sauvegarde une notification dans l'historique in-app"""
        try:
            await self.supabase.insert(
                "notifications",
                {
                    "user_id": user_id,
                    "type": notification_type.value,
                    "title": title,
                    "body": body,
                    "data": data,
                },
            )
            logger.info(f"Notification {notification_type.value} sauvegardée pour l'utilisateur {user_id}")
        except Exception as e:
            logger.error(f"Erreur lors de la sauvegarde de la notification: {e}")

    async def _get_user_preferences(self, user_id: str) -> Dict[str, Any]:
        """Récupère les préférences de notification de l'utilisateur"""
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    self.supabase._get_url("notification_preferences"),
                    params={"user_id": f"eq.{user_id}"},
                    headers=self.supabase._get_headers(),
                    timeout=10,
                )
                response.raise_for_status()
                rows = response.json()

                if rows:
                    return rows[0]

                # Retourner les valeurs par défaut si aucune préférence n'existe
                return {
                    "push_enabled": True,
                    "analysis_complete_push": True,
                    "analysis_error_push": True,
                    "content_saved_push": False,
                }
        except Exception as e:
            logger.error(f"Erreur lors de la récupération des préférences: {e}")
            return {
                "push_enabled": True,
                "analysis_complete_push": True,
                "analysis_error_push": True,
                "content_saved_push": False,
            }

    async def _get_active_push_tokens(self, user_id: str) -> List[str]:
        """Récupère les tokens push actifs de l'utilisateur"""
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    self.supabase._get_url("push_tokens"),
                    params={
                        "user_id": f"eq.{user_id}",
                        "is_active": "eq.true",
                        "select": "expo_push_token",
                    },
                    headers=self.supabase._get_headers(),
                    timeout=10,
                )
                response.raise_for_status()
                rows = response.json()
                return [row["expo_push_token"] for row in rows]
        except Exception as e:
            logger.error(f"Erreur lors de la récupération des tokens push: {e}")
            return []

    async def _send_expo_push_notifications(
        self,
        tokens: List[str],
        title: str,
        body: str,
        data: Dict[str, Any],
    ) -> None:
        """Envoie des notifications via Expo Push API"""
        if not tokens:
            return

        messages = [
            {
                "to": token,
                "sound": "default",
                "title": title,
                "body": body,
                "data": data,
                "priority": "high",
                "channelId": "default",
            }
            for token in tokens
        ]

        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    EXPO_PUSH_API_URL,
                    json=messages,
                    headers={
                        "Accept": "application/json",
                        "Accept-Encoding": "gzip, deflate",
                        "Content-Type": "application/json",
                    },
                    timeout=30,
                )
                response.raise_for_status()
                result = response.json()

                # Gérer les tokens invalides
                if "data" in result:
                    await self._handle_push_response(tokens, result["data"])

                logger.info(f"Notifications push envoyées à {len(tokens)} appareils")

        except Exception as e:
            logger.error(f"Erreur lors de l'envoi des notifications Expo: {e}")

    async def _handle_push_response(
        self,
        tokens: List[str],
        responses: List[Dict[str, Any]],
    ) -> None:
        """
        Gère les réponses de l'API Expo Push et désactive les tokens invalides.
        """
        for token, response in zip(tokens, responses):
            if response.get("status") == "error":
                error_type = response.get("details", {}).get("error")
                error_message = response.get("message", "Unknown error")
                logger.warning(f"Expo push error: type={error_type}, message={error_message}")
                logger.debug(f"Full Expo response: {response}")

                if error_type in ("DeviceNotRegistered", "InvalidCredentials"):
                    logger.warning(f"Token invalide, désactivation: {token[:20]}...")
                    await self._deactivate_push_token(token)

    async def _deactivate_push_token(self, token: str) -> None:
        """Désactive un token push invalide"""
        try:
            async with httpx.AsyncClient() as client:
                await client.patch(
                    self.supabase._get_url("push_tokens"),
                    params={"expo_push_token": f"eq.{token}"},
                    json={"is_active": False},
                    headers=self.supabase._get_headers(),
                    timeout=10,
                )
                logger.info(f"Token push désactivé: {token[:20]}...")
        except Exception as e:
            logger.error(f"Erreur lors de la désactivation du token: {e}")

    @staticmethod
    def extract_error_code(error_msg: str) -> str:
        """
        Extrait un code d'erreur à partir du message d'erreur.
        """
        error_msg_lower = error_msg.lower()

        if "private" in error_msg_lower or "privée" in error_msg_lower:
            return ErrorCode.PRIVATE_VIDEO.value
        if "ip" in error_msg_lower and "block" in error_msg_lower:
            return ErrorCode.IP_BLOCKED.value
        if "unsupported" in error_msg_lower or "non supportée" in error_msg_lower:
            return ErrorCode.UNSUPPORTED_URL.value
        if "download" in error_msg_lower or "télécharg" in error_msg_lower:
            return ErrorCode.DOWNLOAD_ERROR.value
        if "inférence" in error_msg_lower or "inference" in error_msg_lower:
            return ErrorCode.INFERENCE_ERROR.value

        return ErrorCode.UNKNOWN_ERROR.value
