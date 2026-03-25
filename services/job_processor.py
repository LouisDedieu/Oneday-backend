"""
Service de traitement des jobs d'analyse vidéo
"""
import os
import asyncio
import logging
import tempfile
import json
import shutil
from typing import Dict, Optional
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor

from models.schemas import AnalyzeUrlRequest
from services.ml_service import ml_service
from services.supabase_service import SupabaseService
from services.sse_service import job_manager
from services.notification_service import NotificationService
from downloader import (
    download_content,
    ContentType,
    UnsupportedURLError,
    PrivateVideoError,
    IPBlockedError,
    DownloadError,
    VideoTooLongError,
    BlogExtractionError,
    _resolve_tiktok_url,
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
        tmp_dir: Optional[str] = None

        try:
            # ── Étape 0.5 : Résolution URL ──────────────────────────────────────
            # URL résolue complète pour téléchargement et sauvegarde
            loop = asyncio.get_running_loop()
            full_url = await loop.run_in_executor(
                None, _resolve_tiktok_url, request.url
            )
            if not full_url:
                full_url = request.url
            
            # Supprimer les query params (ex: ?_r=1&_t=...)
            from urllib.parse import urlparse, urlunparse
            parsed = urlparse(full_url)
            full_url = urlunparse((parsed.scheme, parsed.netloc, parsed.path, '', '', ''))
            
            logger.info("[job %s] URL complète : %s", job_id, full_url)

            # Créer le job dans Supabase avec l'URL résolue
            if self.supabase.is_configured():
                await self.supabase.create_job(job_id, full_url, request.user_id)

            # ── Étape 0.75 : Vérification de doublon ──────────────────────────
            if self.supabase.is_configured():
                existing = await self.supabase.find_trip_by_source_url(full_url)
                if not existing:
                    existing = await self.supabase.find_city_by_source_url(full_url)

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
                            "source_url": full_url,
                            "cloned": True,
                            "cloned_from": entity_id,
                        }
                        job_manager.update_job_status(job_id, "done", result=response_data)
                        await job_manager.send_sse_update(
                            job_id, "done", {"result": response_data, "progress": 100}
                        )
                        update_data: Dict = {
                            "status": "done",
                            "entity_type": entity_type,
                            "completed_at": datetime.utcnow().isoformat(),
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

            logger.info("[job %s] Téléchargement de %s", job_id, full_url)

            # Générer un répertoire unique pour le job
            tmp_dir = os.path.join(tempfile.gettempdir(), f"bombo_{job_id}")
            os.makedirs(tmp_dir, exist_ok=True)

            download_result = None
            try:
                download_result = await download_content(
                    full_url,
                    tmp_dir,
                    cookies_file=request.cookies_file or self.default_cookies_file,
                    proxy=request.proxy or self.default_proxy,
                )
                content_type = download_result.content_type
                file_paths = download_result.file_paths
                duration = download_result.duration_seconds
                image_count = download_result.image_count

            except UnsupportedURLError:
                error_msg = "URL non supportée (accepte TikTok, Instagram, ou articles de blog)."
                await self._handle_error(job_id, error_msg, request.user_id, request.url)
                return
            except VideoTooLongError as exc:
                await self._handle_video_too_long_error(job_id, str(exc), request.user_id, request.url)
                return
            except BlogExtractionError as exc:
                error_msg = f"Impossible d'extraire le contenu de l'article: {exc}"
                await self._handle_error(job_id, error_msg, request.user_id, request.url)
                return
            except (PrivateVideoError, IPBlockedError, DownloadError) as exc:
                is_instagram_post = "/p/" in full_url.lower() and "instagram.com" in full_url.lower()
                if is_instagram_post and isinstance(exc, PrivateVideoError):
                    logger.info("[job %s] Instagram /p/ inaccessible — tentative gallery-dl", job_id)
                    try:
                        from downloader import _download_instagram_gallery_dl, _download_carousel_instaloader
                        loop = asyncio.get_event_loop()

                        # Tentative 1: gallery-dl
                        file_paths, image_count = await loop.run_in_executor(
                            None,
                            _download_instagram_gallery_dl,
                            full_url,
                            tmp_dir,
                        )
                        logger.info("[job %s] Gallery-dl: %d images récupérées", job_id, len(file_paths))

                        # Tentative 2: instaloader si gallery-dl a échoué
                        if not file_paths:
                            logger.info("[job %s] Gallery-dl a échoué — tentative instaloader", job_id)
                            file_paths, image_count = await loop.run_in_executor(
                                None,
                                _download_carousel_instaloader,
                                full_url,
                                tmp_dir,
                            )
                            logger.info("[job %s] Instaloader: %d images récupérées", job_id, len(file_paths))

                        if not file_paths:
                            logger.warning("[job %s] Aucun téléchargement possible — abandon du fallback", job_id)
                            await self._handle_error(job_id, str(exc), request.user_id, request.url)
                            return

                        content_type = ContentType.CAROUSEL
                        duration = 0.0
                    except Exception:
                        await self._handle_error(job_id, str(exc), request.user_id, request.url)
                        return
                else:
                    await self._handle_error(job_id, str(exc), request.user_id, request.url)
                    return

            # Log according to content type
            if content_type == ContentType.BLOG:
                logger.info("[job %s] Blog détecté : %d mots, ~%d min de lecture", 
                    job_id, getattr(download_result, 'word_count', 0), getattr(download_result, 'estimated_read_time', 0))
            elif content_type == ContentType.CAROUSEL:
                logger.info("[job %s] Carrousel détecté : %d images", job_id, image_count)
            else:
                logger.info("[job %s] Vidéo téléchargée : %s", job_id, file_paths[0] if file_paths else "?")

            await job_manager.send_sse_update(
                job_id, "downloading", {"progress": 50}
            )

            # ── Étape 2 : Détection du type de contenu ───────────────────────
            entity_type_override = getattr(request, 'entity_type_override', None)

            if entity_type_override and entity_type_override in ('trip', 'city'):
                entity_type = entity_type_override
                logger.info("[job %s] Type forcé par l'utilisateur : %s", job_id, entity_type)
            else:
                if content_type == ContentType.BLOG:
                    # For blogs, analyze the content to determine entity type
                    await job_manager.send_sse_update(
                        job_id, "analyzing", {"progress": 55, "message": "Analyse de l'article..."}
                    )
                    loop = asyncio.get_event_loop()
                    input_path = file_paths[0] if file_paths else ""
                    entity_type = await loop.run_in_executor(
                        _executor, ml_service.detect_entity_type, input_path
                    )
                    logger.info("[job %s] Blog analysé → %s", job_id, entity_type)
                else:
                    await job_manager.send_sse_update(
                        job_id, "analyzing", {"progress": 55, "message": "Détection du type..."}
                    )
                    loop = asyncio.get_event_loop()
                    input_path = file_paths[0] if file_paths else ""
                    entity_type = await loop.run_in_executor(
                        _executor, ml_service.detect_entity_type, input_path
                    )

            # ── Étape 3 : Analyse selon le type ──────────────────────────────
            await job_manager.send_sse_update(job_id, "analyzing", {"progress": 60})
            if self.supabase.is_configured():
                await self.supabase.update_job(job_id, {
                    "status": "analyzing",
                })

            logger.info("[job %s] Début de l'inférence (%s)", job_id, entity_type)

            try:
                loop = asyncio.get_event_loop()
                if content_type == ContentType.CAROUSEL:
                    if entity_type == 'city':
                        result, duration = await loop.run_in_executor(
                            _executor, ml_service.run_city_inference_from_images, file_paths
                        )
                    else:
                        result, duration = await loop.run_in_executor(
                            _executor, ml_service.run_inference_from_images, file_paths
                        )
                elif content_type == ContentType.BLOG:
                    # For blogs, use the text content for inference
                    input_path = file_paths[0] if file_paths else ""
                    if entity_type == 'city':
                        result, duration = await loop.run_in_executor(
                            _executor, ml_service.run_city_inference, input_path
                        )
                    else:
                        result, duration = await loop.run_in_executor(
                            _executor, ml_service.run_inference, input_path
                        )
                else:
                    input_path = file_paths[0] if file_paths else ""
                    if entity_type == 'city':
                        result, duration = await loop.run_in_executor(
                            _executor, ml_service.run_city_inference, input_path
                        )
                    else:
                        result, duration = await loop.run_in_executor(
                            _executor, ml_service.run_inference, input_path
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
                # Ajouter l'URL source (résolue) et type de contenu au résultat avant de sauvegarder
                result["source_url"] = full_url
                result["content_type"] = content_type.value
                result["image_count"] = image_count
                
                # Add blog-specific fields
                if content_type == ContentType.BLOG:
                    result["word_count"] = getattr(download_result, 'word_count', None)
                    result["estimated_read_time"] = getattr(download_result, 'estimated_read_time', None)

                if entity_type == 'city':
                    city_id = await self.supabase.create_city(
                        result, job_id, request.user_id
                    )
                    if city_id is None:
                        await self._handle_error(
                            job_id,
                            "Échec de la sauvegarde dans la base de données. Vérifiez les logs du serveur.",
                            request.user_id,
                            full_url,
                        )
                        return
                else:
                    trip_id = await self.supabase.create_trip(
                        result, job_id, request.user_id
                    )
                    if trip_id is None:
                        await self._handle_error(
                            job_id,
                            "Échec de la sauvegarde dans la base de données. Vérifiez les logs du serveur.",
                            request.user_id,
                            full_url,
                        )
                        return

            # ── Terminé ─────────────────────────────────────────────────────
            response_data = {
                "job_id": job_id,
                "trip_id": trip_id,
                "city_id": city_id,
                "entity_type": entity_type,
                "content_type": content_type.value,
                "image_count": image_count,
                "duration_seconds": duration,
                "raw_json": result,
                "source_url": full_url,
            }

            # Add blog-specific fields to response
            if content_type == ContentType.BLOG:
                response_data["word_count"] = getattr(download_result, 'word_count', None)
                response_data["estimated_read_time"] = getattr(download_result, 'estimated_read_time', None)

            job_manager.update_job_status(job_id, "done", result=response_data)
            await job_manager.send_sse_update(
                job_id, "done", {"result": response_data, "progress": 100}
            )

            if self.supabase.is_configured():
                update_data: Dict = {
                    "status": "done",
                    "content_type": content_type.value,
                    "image_count": image_count,
                    "entity_type": entity_type,
                }
                if city_id:
                    update_data["city_id"] = city_id
                try:
                    await self.supabase.update_job(job_id, update_data)
                except Exception as update_exc:
                    logger.error("[job %s] Erreur mise à jour job dans Supabase: %s", job_id, update_exc)

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
            # Supprimer le répertoire temporaire
            if tmp_dir and os.path.exists(tmp_dir):
                try:
                    shutil.rmtree(tmp_dir)
                    logger.debug(
                        "[job %s] Répertoire temporaire supprimé : %s", job_id, tmp_dir
                    )
                except OSError as e:
                    logger.warning(
                        "[job %s] Impossible de supprimer le répertoire temp : %s",
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
