"""
Routes pour l'analyse de vidéos
"""
import uuid
import json
import asyncio
import logging
from typing import Optional
from fastapi import APIRouter, BackgroundTasks, HTTPException
from fastapi.responses import StreamingResponse
from datetime import datetime

from models.schemas import AnalyzeUrlRequest, JobResponse, JobStatusResponse
from models.errors import ErrorCode, get_error_message
from services.ml_service import ml_service
from services.sse_service import job_manager
from services.job_processor import JobProcessor
from utils.auth import get_current_user_id
from fastapi import Depends

logger = logging.getLogger("bombo.api.analyze")

router = APIRouter(prefix="/analyze", tags=["analyze"])

_job_processor: Optional[JobProcessor] = None


def set_job_processor(processor: JobProcessor):
    """Configure le processeur de jobs (appelé au démarrage de l'app)"""
    global _job_processor
    _job_processor = processor


@router.post("/url", status_code=202, response_model=JobResponse)
async def analyze_video_url(
    request: AnalyzeUrlRequest,
    background_tasks: BackgroundTasks,
    user_id: str = Depends(get_current_user_id),
) -> JobResponse:
    """
    Démarre l'analyse en arrière-plan et retourne immédiatement un job_id.
    Le client doit ensuite se connecter à /analyze/stream/{job_id} pour
    recevoir les mises à jour en temps réel via Server-Sent Events.
    """
    if not _job_processor:
        raise HTTPException(503, detail={
            "error_code": ErrorCode.SERVICE_UNAVAILABLE,
            "message": get_error_message(ErrorCode.SERVICE_UNAVAILABLE),
        })
    if not ml_service.is_ready():
        raise HTTPException(503, detail={
            "error_code": ErrorCode.MODEL_NOT_LOADED,
            "message": get_error_message(ErrorCode.MODEL_NOT_LOADED),
        })

    job_id = str(uuid.uuid4())
    job_manager.create_job(job_id, user_id)

    background_tasks.add_task(_job_processor.process_url_job, job_id, request)

    logger.info("Job %s créé pour %s", job_id, request.url)
    return JobResponse(job_id=job_id)


@router.get("/stream/{job_id}")
async def stream_job_status(
    job_id: str,
    user_id: str = Depends(get_current_user_id),
):
    """Stream SSE des mises à jour du job d'analyse."""
    job = job_manager.get_job(job_id)
    if not job or job.get("user_id") != user_id:
        raise HTTPException(404, detail={
            "error_code": ErrorCode.JOB_NOT_FOUND,
            "message": get_error_message(ErrorCode.JOB_NOT_FOUND),
        })

    queue = asyncio.Queue()
    job_manager.add_sse_queue(job_id, queue)

    async def event_generator():
        try:
            # Envoyer l'état actuel immédiatement
            current_status = {
                "job_id": job_id,
                "status": job["status"],
                "timestamp": datetime.utcnow().isoformat(),
            }

            if job["status"] == "done" and job.get("result"):
                current_status["result"] = job["result"]
            elif job["status"] == "error" and job.get("error"):
                current_status["error"] = job["error"]

            yield f"data: {json.dumps(current_status)}\n\n"

            # Si déjà terminé, arrêter
            if job["status"] in ["done", "error"]:
                return

            # Attendre les mises à jour avec heartbeat
            while True:
                try:
                    message = await asyncio.wait_for(queue.get(), timeout=10.0)
                    yield f"data: {json.dumps(message)}\n\n"
                    if message.get("status") in ["done", "error"]:
                        break
                except asyncio.TimeoutError:
                    # Heartbeat pour garder la connexion active
                    yield ": heartbeat\n\n"

        except asyncio.CancelledError:
            logger.info(f"Client SSE déconnecté pour job {job_id}")
        finally:
            job_manager.remove_sse_queue(job_id, queue)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/status/{job_id}", response_model=JobStatusResponse)
async def get_job_status(
    job_id: str,
    user_id: str = Depends(get_current_user_id),
) -> JobStatusResponse:
    """Fallback polling — préférer /analyze/stream/{job_id}."""
    job = job_manager.get_job(job_id)
    if not job or job.get("user_id") != user_id:
        raise HTTPException(404, detail={
            "error_code": ErrorCode.JOB_NOT_FOUND,
            "message": get_error_message(ErrorCode.JOB_NOT_FOUND),
        })

    return JobStatusResponse(
        job_id=job_id,
        status=job["status"],
        result=job.get("result"),
        error=job.get("error"),
    )
