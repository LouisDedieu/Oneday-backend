"""
utils/url_normalizer.py — Normalisation des URLs TikTok / Instagram
Extrait un identifiant canonique pour détecter les doublons.

Formes canoniques :
  TikTok  → tiktok.com/video/{id}
  Instagram → instagram.com/reel/{shortcode}  ou  instagram.com/p/{shortcode}
"""

import re
import logging
from typing import Optional
from urllib.parse import urlparse, parse_qs

import httpx

logger = logging.getLogger("bombo.url_normalizer")

# ── Patterns de détection ─────────────────────────────────────────────────────

# TikTok : URL longue avec ID numérique
_TIKTOK_VIDEO_ID_RE = re.compile(
    r"tiktok\.com/.*/video/(\d+)", re.IGNORECASE
)

# TikTok : URL courte (vm.tiktok.com, vt.tiktok.com)
_TIKTOK_SHORT_RE = re.compile(
    r"^https?://(vm|vt)\.tiktok\.com/", re.IGNORECASE
)

# Instagram Reel
_INSTAGRAM_REEL_RE = re.compile(
    r"instagram\.com/reel/([A-Za-z0-9_-]+)", re.IGNORECASE
)

# Instagram Post
_INSTAGRAM_POST_RE = re.compile(
    r"instagram\.com/p/([A-Za-z0-9_-]+)", re.IGNORECASE
)


# ── Résolution des URLs courtes ───────────────────────────────────────────────

async def _resolve_short_url(url: str) -> Optional[str]:
    """
    Suit les redirections d'une URL courte pour obtenir l'URL longue.
    Retourne None si la résolution échoue.
    """
    try:
        async with httpx.AsyncClient(
            follow_redirects=True,
            timeout=10,
            headers={"User-Agent": "Mozilla/5.0"},
        ) as client:
            response = await client.head(url)
            final_url = str(response.url)
            logger.debug("URL courte %s → %s", url, final_url)
            return final_url
    except Exception as e:
        logger.warning("Impossible de résoudre l'URL courte %s : %s", url, e)
        return None


# ── Normalisation ─────────────────────────────────────────────────────────────

def _normalize_from_long_url(url: str) -> Optional[str]:
    """
    Extrait la forme canonique depuis une URL longue (déjà résolue).
    Retourne None si le format n'est pas reconnu.
    """
    # TikTok : extraire l'ID vidéo
    m = _TIKTOK_VIDEO_ID_RE.search(url)
    if m:
        return f"tiktok.com/video/{m.group(1)}"

    # Instagram Reel
    m = _INSTAGRAM_REEL_RE.search(url)
    if m:
        return f"instagram.com/reel/{m.group(1)}"

    # Instagram Post
    m = _INSTAGRAM_POST_RE.search(url)
    if m:
        return f"instagram.com/p/{m.group(1)}"

    return None


async def normalize_url(url: str) -> str:
    """
    Retourne une URL canonique pour comparaison de doublons.

    - TikTok longue → tiktok.com/video/{id}
    - TikTok courte (vm/vt.tiktok.com) → résolution → tiktok.com/video/{id}
    - Instagram → instagram.com/reel/{shortcode}
    - Fallback → URL nettoyée (sans query params et fragments)

    Exemples :
        https://www.tiktok.com/@user/video/7123456789 → tiktok.com/video/7123456789
        https://vm.tiktok.com/ZMhAbCdEf/ → tiktok.com/video/7123456789
        https://www.instagram.com/reel/CxYz123/ → instagram.com/reel/CxYz123
    """
    url = url.strip()

    # 1. Essayer de normaliser directement (URL longue)
    canonical = _normalize_from_long_url(url)
    if canonical:
        logger.debug("URL normalisée : %s → %s", url, canonical)
        return canonical

    # 2. URL courte TikTok → résoudre les redirections
    if _TIKTOK_SHORT_RE.match(url):
        resolved = await _resolve_short_url(url)
        if resolved:
            canonical = _normalize_from_long_url(resolved)
            if canonical:
                logger.debug("URL courte normalisée : %s → %s → %s", url, resolved, canonical)
                return canonical

    # 3. Fallback : nettoyer l'URL (retirer query params et fragments)
    parsed = urlparse(url)
    cleaned = f"{parsed.scheme}://{parsed.netloc}{parsed.path}".rstrip("/")
    logger.debug("URL fallback : %s → %s", url, cleaned)
    return cleaned
