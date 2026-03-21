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

    # ── Gemini API ────────────────────────────────────────────────────────────
    GEMINI_API_KEY: str = ""            # Rétro-compat (clé unique)
    GEMINI_API_KEYS: str = ""           # Clés multiples séparées par des virgules
    GEMINI_MODEL_ID: str = "gemini-2.0-flash"

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

    @property
    def gemini_api_key_list(self) -> list[str]:
        """Retourne la liste des clés API Gemini disponibles."""
        keys: list[str] = []
        # Priorité aux clés multiples
        if self.GEMINI_API_KEYS:
            keys = [k.strip() for k in self.GEMINI_API_KEYS.split(",") if k.strip()]
        # Fallback sur la clé unique (rétro-compat)
        if not keys and self.GEMINI_API_KEY:
            keys = [self.GEMINI_API_KEY]
        return keys


settings = Settings()