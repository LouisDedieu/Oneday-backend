"""
main.py – BOMBO Travel Video Analyzer API (Version refactorisée)
FastAPI + Qwen2-VL-7B + yt-dlp + Server-Sent Events
"""
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from config import settings
from services.ml_service import ml_service
from services.supabase_service import SupabaseService
from services.job_processor import JobProcessor
from api import analyze, trips, inbox, profile, review, cities, city_review, notifications

# ── Logging ──────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s – %(message)s",
)
logger = logging.getLogger("bombo.main")


# ── Services globaux ─────────────────────────────────────────────────────────

supabase_service = SupabaseService(
    url=settings.supabase_url,
    key=settings.SUPABASE_SERVICE_ROLE_KEY,
)

job_processor = JobProcessor(
    supabase_service=supabase_service,
    cookies_file=settings.COOKIES_FILE,
    proxy=settings.PROXY_URL,
)


# ── Cycle de vie du serveur ───────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Chargement du modèle UNE SEULE FOIS au démarrage."""
    # Initialisation du client Gemini
    ml_service.load_model()

    # Configuration des routes avec les services
    analyze.set_job_processor(job_processor)
    trips.set_supabase_service(supabase_service)
    inbox.set_supabase_service(supabase_service)
    profile.set_supabase_service(supabase_service)
    review.set_supabase_service(supabase_service)
    cities.set_supabase_service(supabase_service)
    city_review.set_supabase_service(supabase_service)
    notifications.set_supabase_service(supabase_service)

    logger.info("Application initialisée et prête ✓")

    yield  # ← le serveur tourne ici

    # Nettoyage
    job_processor.shutdown()
    ml_service.unload_model()
    logger.info("Application arrêtée proprement.")


# ── Application FastAPI ───────────────────────────────────────────────────────

app = FastAPI(
    title="BOMBO – Travel Video Analyzer API",
    description="API d'analyse de vidéos de voyage avec ML",
    version="2.0.0",
    lifespan=lifespan,
)

# ── CORS ──────────────────────────────────────────────────────────────────────

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",  # Vite dev
        "http://localhost:3000",  # CRA / Next dev
        "*",  # ← restreindre en production
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Routes ────────────────────────────────────────────────────────────────────

@app.get("/health")
async def health_check():
    """Vérification de l'état de santé de l'API"""
    return {
        "status": "ok" if ml_service.is_ready() else "loading",
        "model": settings.GEMINI_MODEL_ID,
        "device": ml_service.device or "unknown",
        "model_loaded": ml_service.is_ready(),
        "supabase_connected": supabase_service.is_configured(),
    }


# Inclusion des routers
app.include_router(analyze.router)
app.include_router(trips.router)
app.include_router(inbox.router)
app.include_router(profile.router)
app.include_router(review.router)
app.include_router(cities.router)
app.include_router(city_review.router)
app.include_router(notifications.router)


# ── Point d'entrée ────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host=settings.HOST, port=settings.PORT)
