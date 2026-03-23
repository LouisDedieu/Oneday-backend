"""
Service de traitement des jobs d'analyse vidéo
"""
import os
import asyncio
import logging
import tempfile
import json
from typing import Dict, Optional
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor

from models.schemas import AnalyzeUrlRequest
from services.ml_service import ml_service
from utils.url_normalizer import normalize_url
from services.supabase_service import SupabaseService
from services.sse_service import job_manager
from services.notification_service import NotificationService
from downloader import (
    download_video,
    UnsupportedURLError,
    PrivateVideoError,
    IPBlockedError,
    DownloadError,
    VideoTooLongError,
)

logger = logging.getLogger("bombo.job_processor")

# Exécuteur dédié pour l'inférence (bloquante)
_executor = ThreadPoolExecutor(max_workers=1)


class JobProcessor:
    """Service de traitement des jobs d'analyse"""

    def __init__(
        self,
        supabase_service: SupabaseService,
        cookies_file: Optional[str] = None,
        proxy: Optional[str] = None,
    ):
        self.supabase = supabase_service
        self.default_cookies_file = cookies_file
        self.default_proxy = proxy
        self.notification_service = NotificationService(supabase_service)

    async def process_url_job(self, job_id: str, request: AnalyzeUrlRequest) -> None:
        """
        Exécute l'analyse en arrière-plan et envoie des mises à jour SSE.
        Supporte la détection automatique du type (trip/city) ou override manuel.
        """
        tmp_path: Optional[str] = None

        try:
            # Créer le job dans Supabase
            if self.supabase.is_configured():
                await self.supabase.create_job(job_id, request.url, request.user_id)

            # ── Étape 0.5 : Vérification de doublon ──────────────────────────
            normalized_url: Optional[str] = None
            if self.supabase.is_configured():
                normalized_url = await normalize_url(request.url)
                logger.info("[job %s] URL normalisée : %s", job_id, normalized_url)

                existing = await self.supabase.find_trip_by_source_url(normalized_url)
                if not existing:
                    existing = await self.supabase.find_city_by_source_url(normalized_url)

                if existing:
                    entity_type = existing["type"]
                    entity_id = existing["id"]
                    logger.info("[job %s] Doublon trouvé : %s %s → clonage", job_id, entity_type, entity_id)

                    if entity_type == "trip":
                        new_id = await self.supabase.clone_trip_for_user(entity_id, job_id, request.user_id)
                    else:
                        new_id = await self.supabase.clone_city_for_user(entity_id, job_id, request.user_id)

                    if new_id:
                        response_data = {
                            "job_id": job_id,
                            "trip_id": new_id if entity_type == "trip" else None,
                            "city_id": new_id if entity_type == "city" else None,
                            "entity_type": entity_type,
                            "duration_seconds": 0,
                            "source_url": request.url,
                            "cloned": True,
                            "cloned_from": entity_id,
                        }
                        job_manager.update_job_status(job_id, "done", result=response_data)
                        await job_manager.send_sse_update(
                            job_id, "done", {"result": response_data, "progress": 100}
                        )
                        update_data: Dict = {
                            "status": "done",
                            "completed_at": datetime.utcnow().isoformat(),
                            "duration_seconds": 0,
                            "entity_type": entity_type,
                        }
                        if entity_type == "city":
                            update_data["city_id"] = new_id
                        await self.supabase.update_job(job_id, update_data)
                        logger.info("[job %s] Cloné depuis %s %s ✓", job_id, entity_type, entity_id)
                        return
                    else:
                        logger.warning("[job %s] Clonage échoué, flow classique", job_id)

            # ── Étape 1 : Téléchargement ─────────────────────────────────────
            await job_manager.send_sse_update(job_id, "downloading", {"progress": 0})
            if self.supabase.is_configured():
                await self.supabase.update_job(job_id, {"status": "downloading"})

            logger.info("[job %s] Téléchargement de %s", job_id, request.url)

            # Générer un chemin unique
            tmp_path = os.path.join(tempfile.gettempdir(), f"bombo_{job_id}.mp4")

            try:
                await download_video(
                    request.url,
                    tmp_path,
                    cookies_file=request.cookies_file or self.default_cookies_file,
                    proxy=request.proxy or self.default_proxy,
                )
                output_path = tmp_path

            except UnsupportedURLError:
                error_msg = "URL non supportée (accepte TikTok, Instagram Reels)."
                await self._handle_error(job_id, error_msg, request.user_id, request.url)
                return
            except VideoTooLongError as exc:
                await self._handle_video_too_long_error(job_id, str(exc), request.user_id, request.url)
                return
            except (PrivateVideoError, IPBlockedError, DownloadError) as exc:
                await self._handle_error(job_id, str(exc), request.user_id, request.url)
                return

            logger.info("[job %s] Vidéo téléchargée : %s", job_id, output_path)
            await job_manager.send_sse_update(
                job_id, "downloading", {"progress": 50}
            )

            # ── Étape 2 : Détection du type de contenu ───────────────────────
            entity_type_override = getattr(request, 'entity_type_override', None)

            if entity_type_override and entity_type_override in ('trip', 'city'):
                entity_type = entity_type_override
                logger.info("[job %s] Type forcé par l'utilisateur : %s", job_id, entity_type)
            else:
                # Auto-détection
                await job_manager.send_sse_update(
                    job_id, "analyzing", {"progress": 55, "message": "Détection du type..."}
                )
                loop = asyncio.get_event_loop()
                entity_type = await loop.run_in_executor(
                    _executor, ml_service.detect_entity_type, output_path
                )

            # ── Étape 3 : Analyse selon le type ──────────────────────────────
            await job_manager.send_sse_update(job_id, "analyzing", {"progress": 60})
            if self.supabase.is_configured():
                await self.supabase.update_job(job_id, {
                    "status": "analyzing",
                    "entity_type": entity_type,
                })

            logger.info("[job %s] Début de l'inférence (%s)", job_id, entity_type)

            try:
                loop = asyncio.get_event_loop()
                if entity_type == 'city':
                    result, duration = await loop.run_in_executor(
                        _executor, ml_service.run_city_inference, output_path
                    )
                else:
                    result, duration = await loop.run_in_executor(
                        _executor, ml_service.run_inference, output_path
                    )
                await job_manager.send_sse_update(
                    job_id, "analyzing", {"progress": 75}
                )

            except Exception as exc:
                logger.exception("[job %s] Erreur lors de l'inférence", job_id)
                error_msg = f"Erreur d'inférence : {exc}"
                await self._handle_error(job_id, error_msg, request.user_id, request.url)
                return

            # ── Étape Intermédiaire : Sauvegarde locale du raw JSON ──────────
            results_dir = os.path.join(os.path.dirname(__file__), "results")
            os.makedirs(results_dir, exist_ok=True)

            json_path = os.path.join(results_dir, f"{job_id}.json")
            with open(json_path, "w", encoding="utf-8") as f:
                json.dump(result, f, ensure_ascii=False, indent=2)

            logger.info("[job %s] raw_json sauvegardé localement : %s", job_id, json_path)

            # ── Étape 4 : Sauvegarde dans Supabase ──────────────────────────
            trip_id = None
            city_id = None

            if self.supabase.is_configured():
                await job_manager.send_sse_update(
                    job_id, "analyzing", {"progress": 90, "message": "Sauvegarde..."}
                )
                # Ajouter l'URL source au résultat avant de sauvegarder
                result["source_url"] = request.url
                if normalized_url:
                    result["normalized_source_url"] = normalized_url

                if entity_type == 'city':
                    city_id = await self.supabase.create_city(
                        result, job_id, request.user_id
                    )
                else:
                    trip_id = await self.supabase.create_trip(
                        result, job_id, request.user_id
                    )

            # ── Terminé ─────────────────────────────────────────────────────
            response_data = {
                "job_id": job_id,
                "trip_id": trip_id,
                "city_id": city_id,
                "entity_type": entity_type,
                "duration_seconds": duration,
                "raw_json": result,
                "source_url": request.url,
            }

            job_manager.update_job_status(job_id, "done", result=response_data)
            await job_manager.send_sse_update(
                job_id, "done", {"result": response_data, "progress": 100}
            )

            if self.supabase.is_configured():
                update_data = {
                    "status": "done",
                    "completed_at": datetime.utcnow().isoformat(),
                    "duration_seconds": duration,
                    "entity_type": entity_type,
                }
                if city_id:
                    update_data["city_id"] = city_id
                await self.supabase.update_job(job_id, update_data)

            logger.info("[job %s] Terminé en %.2fs (type=%s)", job_id, duration, entity_type)

            # ── Notification de succès ─────────────────────────────────────
            if request.user_id:
                entity_id = city_id if entity_type == "city" else trip_id
                entity_title = result.get("city_title") if entity_type == "city" else result.get("trip_title")
                if entity_id and entity_title:
                    try:
                        await self.notification_service.notify_analysis_complete(
                            user_id=request.user_id,
                            entity_type=entity_type,
                            entity_id=entity_id,
                            title=entity_title,
                            source_url=request.url,
                        )
                    except Exception as notif_exc:
                        logger.warning("[job %s] Erreur notification succès: %s", job_id, notif_exc)

        except Exception as exc:
            logger.exception("[job %s] Erreur inattendue", job_id)
            error_msg = f"Erreur inattendue : {exc}"
            await self._handle_error(job_id, error_msg, request.user_id, request.url)

        finally:
            # Supprimer le fichier temporaire
            if tmp_path and os.path.exists(tmp_path):
                try:
                    os.unlink(tmp_path)
                    logger.debug(
                        "[job %s] Fichier temporaire supprimé : %s", job_id, tmp_path
                    )
                except OSError as e:
                    logger.warning(
                        "[job %s] Impossible de supprimer le fichier temp : %s",
                        job_id,
                        e,
                    )

    async def _handle_error(
        self,
        job_id: str,
        error_msg: str,
        user_id: Optional[str] = None,
        source_url: Optional[str] = None,
    ) -> None:
        """Gère les erreurs de traitement de job"""
        job_manager.update_job_status(job_id, "error", error=error_msg)
        await job_manager.send_sse_update(job_id, "error", {"error": error_msg})
        if self.supabase.is_configured():
            await self.supabase.update_job(
                job_id, {"status": "error", "error_message": error_msg}
            )

        # Envoyer une notification d'erreur
        if user_id:
            try:
                error_code = NotificationService.extract_error_code(error_msg)
                await self.notification_service.notify_analysis_error(
                    user_id=user_id,
                    job_id=job_id,
                    error_code=error_code,
                    source_url=source_url,
                    error_message=error_msg,
                )
            except Exception as notif_exc:
                logger.warning("[job %s] Erreur notification erreur: %s", job_id, notif_exc)

    async def _handle_video_too_long_error(
        self,
        job_id: str,
        error_msg: str,
        user_id: Optional[str] = None,
        source_url: Optional[str] = None,
    ) -> None:
        """Gère les erreurs de vidéo trop longue"""
        job_manager.update_job_status(job_id, "error", error=error_msg)
        await job_manager.send_sse_update(job_id, "error", {"error": error_msg, "video_too_long": True})
        if self.supabase.is_configured():
            await self.supabase.update_job(
                job_id, {"status": "error", "error_message": error_msg}
            )

    def shutdown(self):
        """Arrête le processeur de jobs"""
        _executor.shutdown(wait=False)
