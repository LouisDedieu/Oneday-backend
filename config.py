"""
config.py — Paramètres centralisés BOMBO
Tous les knobs du modèle et du serveur en un seul endroit.
"""

from pydantic_settings import BaseSettings, SettingsConfigDict  # ← add SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        extra="ignore",       # silently drop unknown env vars
    )

    # ── Modèle IA ─────────────────────────────────────────────────────────────
    MODEL_ID: str = "Qwen/Qwen2-VL-7B-Instruct"

    # ── Optimisations inférence ───────────────────────────────────────────────
    MAX_PIXELS: int = 360 * 420
    FPS: float = 0.3
    MAX_NEW_TOKENS: int = 4096

    # ── Serveur ───────────────────────────────────────────────────────────────
    HOST: str = "0.0.0.0"
    PORT: int = 8000

    # ── Téléchargement ────────────────────────────────────────────────────────
    COOKIES_FILE: str | None = None
    PROXY_URL: str | None = None
    DOWNLOAD_TIMEOUT: int = 120

    # ── Supabase ──────────────────────────────────────────────────────────────
    supabase_url: str = ""
    SUPABASE_SERVICE_ROLE_KEY: str = ""


settings = Settings()