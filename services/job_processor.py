"""
Service de traitement des jobs d'analyse vidéo
"""
import os
import asyncio
import logging
import tempfile
from typing import Optional
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor

from models.schemas import AnalyzeUrlRequest
from services.ml_service import ml_service
from services.supabase_service import SupabaseService
from services.sse_service import job_manager
from downloader import (
    download_video,
    UnsupportedURLError,
    PrivateVideoError,
    IPBlockedError,
    DownloadError,
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

    async def process_url_job(self, job_id: str, request: AnalyzeUrlRequest) -> None:
        """
        Exécute l'analyse en arrière-plan et envoie des mises à jour SSE.
        """
        tmp_path: Optional[str] = None

        try:
            # Créer le job dans Supabase
            if self.supabase.is_configured():
                await self.supabase.create_job(job_id, request.url, request.user_id)

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
                await self._handle_error(job_id, error_msg)
                return
            except (PrivateVideoError, IPBlockedError, DownloadError) as exc:
                await self._handle_error(job_id, str(exc))
                return

            logger.info("[job %s] Vidéo téléchargée : %s", job_id, output_path)
            await job_manager.send_sse_update(
                job_id, "downloading", {"progress": 50}
            )

            # ── Étape 2 : Analyse ────────────────────────────────────────────
            await job_manager.send_sse_update(job_id, "analyzing", {"progress": 50})
            if self.supabase.is_configured():
                await self.supabase.update_job(job_id, {"status": "analyzing"})

            logger.info("[job %s] Début de l'inférence", job_id)

            try:
                loop = asyncio.get_event_loop()
                result, duration = await loop.run_in_executor(
                    _executor, ml_service.run_inference, output_path
                )
                await job_manager.send_sse_update(
                    job_id, "analyzing", {"progress": 75}
                )

            except Exception as exc:
                logger.exception("[job %s] Erreur lors de l'inférence", job_id)
                error_msg = f"Erreur d'inférence : {exc}"
                await self._handle_error(job_id, error_msg)
                return

            # ── Étape 3 : Sauvegarde dans Supabase ──────────────────────────
            trip_id = None
            if self.supabase.is_configured():
                await job_manager.send_sse_update(
                    job_id, "analyzing", {"progress": 90, "message": "Sauvegarde..."}
                )
                # Ajouter l'URL source au résultat avant de sauvegarder
                result["source_url"] = request.url
                trip_id = await self.supabase.create_trip(
                    result, job_id, request.user_id
                )

            # ── Terminé ─────────────────────────────────────────────────────
            itinerary = {
                "job_id": job_id,
                "trip_id": trip_id,
                "duration_seconds": duration,
                "raw_json": result,
                "source_url": request.url,
            }

            job_manager.update_job_status(job_id, "done", result=itinerary)
            await job_manager.send_sse_update(
                job_id, "done", {"result": itinerary, "progress": 100}
            )

            if self.supabase.is_configured():
                await self.supabase.update_job(
                    job_id,
                    {
                        "status": "done",
                        "completed_at": datetime.utcnow().isoformat(),
                        "duration_seconds": duration,
                    },
                )

            logger.info("[job %s] Terminé en %.2fs", job_id, duration)

        except Exception as exc:
            logger.exception("[job %s] Erreur inattendue", job_id)
            error_msg = f"Erreur inattendue : {exc}"
            await self._handle_error(job_id, error_msg)

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

    async def _handle_error(self, job_id: str, error_msg: str) -> None:
        """Gère les erreurs de traitement de job"""
        job_manager.update_job_status(job_id, "error", error=error_msg)
        await job_manager.send_sse_update(job_id, "error", {"error": error_msg})
        if self.supabase.is_configured():
            await self.supabase.update_job(
                job_id, {"status": "error", "error_message": error_msg}
            )

    def shutdown(self):
        """Arrête le processeur de jobs"""
        _executor.shutdown(wait=False)
