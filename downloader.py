"""
downloader.py — Téléchargement de vidéos TikTok / Instagram via yt-dlp
Étape 2 du projet BOMBO

Responsabilités :
  - Valider l'URL (TikTok / Instagram uniquement)
  - Télécharger la vidéo dans un dossier temporaire géré par l'appelant
  - Contourner le blocage TikTok par cascade de stratégies
  - Exposer des exceptions claires pour que main.py renvoie les bons codes HTTP

Stratégie anti-blocage (cascade automatique) :
  Le blocage TikTok est double :
    (a) Empreinte TLS Python détectée  → résolu par curl_cffi (impersonation navigateur)
    (b) Absence de session TikTok      → résolu par cookies du navigateur installé

  Ordre des tentatives :
    1. Chrome  (cookies) + impersonate Chrome 124
    2. Safari  (cookies) + impersonate Safari
    3. Firefox (cookies) + impersonate Chrome 124
    4. Proxy   + impersonate Chrome 124          (si PROXY_URL configuré)
    5. Fichier cookies manuel                    (si cookies_file fourni)
    6. Impersonation seule, sans cookies         (dernier recours)

  curl_cffi requis pour les impersonations : pip install curl_cffi
"""

import re
import asyncio
import logging
from dataclasses import dataclass, field
from pathlib import Path
from functools import partial
from typing import Any

import yt_dlp
from yt_dlp.networking.impersonate import ImpersonateTarget

logger = logging.getLogger("bombo.downloader")

# ── Constantes ────────────────────────────────────────────────────────────────

SUPPORTED_DOMAINS = {
    "tiktok.com",
    "www.tiktok.com",
    "vm.tiktok.com",
    "vt.tiktok.com",
    "instagram.com",
    "www.instagram.com",
}

URL_RE = re.compile(r"https?://(?P<domain>[^/\s]+)", re.IGNORECASE)

DOWNLOAD_TIMEOUT = 120  # secondes
MAX_VIDEO_DURATION = 300  # 5 minutes en secondes

# Cibles d'impersonation curl_cffi
_CHROME = ImpersonateTarget("chrome", "124")
_SAFARI = ImpersonateTarget("safari")

# Mots-clés → blocage IP/bot (on passe à la stratégie suivante)
IP_BLOCK_KEYWORDS = (
    "ip address is blocked",
    "ip is blocked",
    "access denied",
    "unable to download",
    "requested url returned error: 4",  # 403, 429…
)

# Mots-clés → vidéo privée/expirée (on remonte immédiatement)
PRIVATE_KEYWORDS = (
    "private",
    "login required",
    "not available",
    "age-restricted",
    "removed",
    "expired",
    "unavailable",
)


# ── Exceptions ────────────────────────────────────────────────────────────────

class UnsupportedURLError(ValueError):
    """L'URL ne provient pas de TikTok ou Instagram."""

class PrivateVideoError(PermissionError):
    """La vidéo est privée ou le lien a expiré."""

class IPBlockedError(PermissionError):
    """L'IP du serveur est bloquée — toutes les stratégies ont échoué."""

class DownloadError(RuntimeError):
    """Échec générique du téléchargement."""

class VideoTooLongError(ValueError):
    """La vidéo dépasse la durée maximale autorisée."""


# ── Stratégies ────────────────────────────────────────────────────────────────

@dataclass
class Strategy:
    """Représente une tentative de téléchargement avec une configuration donnée."""
    label: str
    impersonate: ImpersonateTarget | None = None
    cookies_from_browser: str | None = None   # "chrome" | "safari" | "firefox"
    cookies_file: str | None = None
    proxy: str | None = None

    def build_ydl_opts(self, output_path: str) -> dict[str, Any]:
        opts: dict[str, Any] = {
            "outtmpl": output_path,
            "format": "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
            "merge_output_format": "mp4",
            "retries": 2,
            "fragment_retries": 2,
            "socket_timeout": 30,
            "quiet": True,
            "no_warnings": False,
            "logger": _YtdlpLogger(),
            "noplaylist": True,
            # max_downloads retiré — noplaylist=True suffit pour les URLs de vidéo unique,
            # et max_downloads=1 levait MaxDownloadsReached après chaque succès.
        }
        if self.impersonate:
            opts["impersonate"] = self.impersonate
        if self.cookies_from_browser:
            # Tuple : (browser, profile, keyring, container) — seul le nom suffit
            opts["cookiesfrombrowser"] = (self.cookies_from_browser,)
        if self.cookies_file:
            opts["cookiefile"] = self.cookies_file
        if self.proxy:
            opts["proxy"] = self.proxy
        return opts


def _build_strategies(
    cookies_file: str | None,
    proxy: str | None,
    has_curl: bool,
) -> list[Strategy]:
    """
    Construit la liste ordonnée des stratégies à tenter.

    Logique :
      - Les cookies de navigateur réel + impersonation = combo le plus efficace
      - On essaie Chrome, Safari, Firefox dans l'ordre
      - Proxy en 4e position (si configuré)
      - Cookies manuels si fournis
      - Impersonation seule en dernier recours
    """
    strategies: list[Strategy] = []

    if has_curl:
        # ── Combinaisons cookies navigateur + impersonation ────────────────────
        for browser in ("chrome", "safari", "firefox"):
            imp = _CHROME if browser != "safari" else _SAFARI
            strategies.append(Strategy(
                label=f"cookies({browser}) + impersonate({imp})",
                impersonate=imp,
                cookies_from_browser=browser,
            ))

        # ── Proxy + impersonation ──────────────────────────────────────────────
        if proxy:
            strategies.append(Strategy(
                label=f"proxy + impersonate(chrome)",
                impersonate=_CHROME,
                proxy=proxy,
            ))
            # Proxy + cookies navigateur (meilleure combinaison si proxy résidentiel)
            strategies.append(Strategy(
                label="proxy + cookies(chrome) + impersonate(chrome)",
                impersonate=_CHROME,
                cookies_from_browser="chrome",
                proxy=proxy,
            ))

    # ── Fichier cookies manuel ─────────────────────────────────────────────────
    if cookies_file:
        imp = _CHROME if has_curl else None
        strategies.append(Strategy(
            label=f"cookies(fichier) + {'impersonate(chrome)' if imp else 'sans impersonation'}",
            impersonate=imp,
            cookies_file=cookies_file,
        ))

    # ── Impersonation seule (dernier recours) ──────────────────────────────────
    if has_curl:
        for imp, name in [(_CHROME, "chrome-124"), (_SAFARI, "safari")]:
            strategies.append(Strategy(
                label=f"impersonate({name}) seul",
                impersonate=imp,
            ))

    # ── Aucune option — tentative brute ───────────────────────────────────────
    if not strategies:
        strategies.append(Strategy(label="aucune option (curl_cffi non installé)"))

    return strategies


# ── Validation ────────────────────────────────────────────────────────────────

def validate_url(url: str) -> str:
    url = url.strip()
    m = URL_RE.match(url)
    if not m:
        raise UnsupportedURLError(f"URL malformée : {url!r}")
    domain = m.group("domain").lower()
    if domain not in SUPPORTED_DOMAINS:
        raise UnsupportedURLError(
            f"Domaine non supporté : '{domain}'. "
            "Seuls TikTok et Instagram sont acceptés."
        )
    return url



# ── Logger yt-dlp ─────────────────────────────────────────────────────────────

class _YtdlpLogger:
    def debug(self, msg: str):
        if not msg.startswith("[debug]"):
            logger.debug("yt-dlp: %s", msg)
    def info(self, msg: str):
        logger.info("yt-dlp: %s", msg)
    def warning(self, msg: str):
        logger.warning("yt-dlp: %s", msg)
    def error(self, msg: str):
        logger.error("yt-dlp: %s", msg)


# ── Détection curl_cffi ───────────────────────────────────────────────────────

def _curl_cffi_available() -> bool:
    try:
        import curl_cffi  # noqa: F401
        return True
    except ImportError:
        return False


# ── Orchestration avec cascade de stratégies ──────────────────────────────────

def _download_sync(
    url: str,
    output_path: str,
    cookies_file: str | None,
    proxy: str | None,
) -> None:
    """
    Tente le téléchargement en cascade de stratégies.
    Les stratégies qui échouent sur un blocage IP/bot passent à la suivante.
    Toute autre erreur (vidéo privée, etc.) remonte immédiatement.
    """
    has_curl = _curl_cffi_available()
    if not has_curl:
        logger.warning(
            "curl_cffi non installé — impersonation désactivée. "
            "Installez : pip install curl_cffi"
        )

    strategies = _build_strategies(cookies_file, proxy, has_curl)
    last_exc: Exception | None = None

    for i, strategy in enumerate(strategies, start=1):
        logger.info("Tentative %d/%d — %s", i, len(strategies), strategy.label)

        opts = strategy.build_ydl_opts(output_path)

        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                try:
                    info = ydl.extract_info(url, download=True)
                except yt_dlp.utils.MaxDownloadsReached:
                    # Levé par yt-dlp APRÈS un download réussi quand max_downloads=1
                    # est atteint — c'est un signal de fin normale, pas une erreur.
                    # Le fichier est déjà écrit sur disque à ce stade.
                    logger.debug("MaxDownloadsReached — téléchargement terminé avec succès.")
                    info = {}
            logger.info(
                "✓ Téléchargé : '%s' (durée=%ss)",
                info.get("title", "?"), info.get("duration", "?"),
            )

            video_duration = info.get("duration")
            if video_duration and video_duration > MAX_VIDEO_DURATION:
                raise VideoTooLongError(
                    f"La vidéo dure {video_duration // 60}min {video_duration % 60}s "
                    f"(max: {MAX_VIDEO_DURATION // 60}min)"
                )

            return  # succès

        except yt_dlp.utils.DownloadError as exc:
            msg = str(exc).lower()
            last_exc = exc

            if any(k in msg for k in PRIVATE_KEYWORDS):
                raise PrivateVideoError(
                    "La vidéo est privée, nécessite une connexion, ou le lien a expiré."
                ) from exc

            if any(k in msg for k in IP_BLOCK_KEYWORDS):
                logger.warning("Bloqué (tentative %d/%d) — stratégie suivante …", i, len(strategies))
                continue

            # Erreur inconnue → remonter immédiatement sans réessayer
            raise DownloadError(f"Erreur yt-dlp inattendue : {exc}") from exc

        except Exception as exc:
            # Erreur lecture cookies navigateur (navigateur absent, profil verrouillé…)
            # → on continue plutôt que de planter
            logger.warning(
                "Erreur non-yt-dlp sur tentative %d (%s) : %s — stratégie suivante …",
                i, strategy.label, exc,
            )
            last_exc = exc
            continue

    # ── Toutes les stratégies ont échoué ──────────────────────────────────────
    raise IPBlockedError(
        f"TikTok/Instagram a bloqué toutes les {len(strategies)} stratégies. "
        "Actions possibles :\n"
        "  1. Exportez les cookies TikTok manuellement (extension 'Get cookies.txt') "
        "et passez le chemin dans cookies_file.\n"
        "  2. Configurez PROXY_URL dans .env avec un proxy résidentiel.\n"
        "  3. Hébergez le backend chez un FAI résidentiel (Hetzner Cloud, etc.)."
    ) from last_exc


# ── Entrée publique (async) ───────────────────────────────────────────────────

async def download_video(
    url: str,
    output_path: str,
    cookies_file: str | None = None,
    proxy: str | None = None,
) -> None:
    """
    Télécharge une vidéo TikTok ou Instagram de façon non-bloquante.

    Args:
        url          : URL publique TikTok ou Instagram
        output_path  : chemin absolu où sauvegarder le fichier mp4
        cookies_file : chemin vers un fichier cookies Netscape (optionnel)
        proxy        : URL proxy SOCKS5/HTTP (optionnel)

    Raises:
        UnsupportedURLError : domaine non autorisé
        PrivateVideoError   : vidéo privée ou lien expiré
        IPBlockedError      : toutes les stratégies ont échoué
        DownloadError       : erreur yt-dlp inattendue
        TimeoutError        : dépassement du timeout global
    """
    validated_url = validate_url(url)
    logger.info("Début téléchargement → %s", validated_url)

    loop = asyncio.get_running_loop()
    fn = partial(_download_sync, validated_url, output_path, cookies_file, proxy)

    try:
        await asyncio.wait_for(
            loop.run_in_executor(None, fn),
            timeout=DOWNLOAD_TIMEOUT,
        )
    except asyncio.TimeoutError:
        raise TimeoutError(f"Téléchargement annulé après {DOWNLOAD_TIMEOUT}s.")

    p = Path(output_path)
    if not p.exists() or p.stat().st_size == 0:
        raise DownloadError("Fichier téléchargé vide ou introuvable.")

    logger.info("Fichier prêt (%d octets) → %s", p.stat().st_size, output_path)