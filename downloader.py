"""
downloader.py — Téléchargement de vidéos TikTok / Instagram via yt-dlp
 Étape 2 du projet BOMBO

 Responsabilités :
   - Valider l'URL (TikTok / Instagram uniquement)
   - Télécharger la vidéo dans un dossier temporaire géré par l'appelant
   - Détecter et télécharger les carrousels Instagram/TikTok
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
import os
import asyncio
import logging
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from functools import partial
from typing import Any
from enum import Enum

import yt_dlp
from yt_dlp.networking.impersonate import ImpersonateTarget

try:
    import httpx
except ImportError:
    httpx = None

try:
    from bs4 import BeautifulSoup
except ImportError:
    BeautifulSoup = None

try:
    from readability import Document
except ImportError:
    Document = None

logger = logging.getLogger("bombo.downloader")


class ContentType(str, Enum):
    VIDEO = "video"
    CAROUSEL = "carousel"
    BLOG = "blog"
    UNKNOWN = "unknown"


@dataclass
class DownloadResult:
    content_type: ContentType = ContentType.VIDEO
    file_paths: list[str] = field(default_factory=list)
    duration_seconds: float = 0.0
    image_count: int = 0
    word_count: int = 0
    estimated_read_time: int = 0

# ── Constantes ────────────────────────────────────────────────────────────────

SUPPORTED_DOMAINS = {
    "tiktok.com",
    "www.tiktok.com",
    "vm.tiktok.com",
    "vt.tiktok.com",
    "instagram.com",
    "www.instagram.com",
}

BLOG_DOMAINS = {
    "medium.com",
    "www.medium.com",
    "substack.com",
    "www.substack.com",
    "wordpress.com",
    "www.wordpress.com",
    "blogspot.com",
    "www.blogspot.com",
}

URL_RE = re.compile(r"https?://(?P<domain>[^/\s]+)", re.IGNORECASE)
BLOG_PATH_RE = re.compile(r"/blog/|/article/", re.IGNORECASE)

DOWNLOAD_TIMEOUT = 120  # secondes
MAX_VIDEO_DURATION = 300  # 5 minutes en secondes
MAX_CAROUSEL_IMAGES = 20  # Limite pour éviter les carrousels trop longs
MAX_BLOG_WORDS = 10000  # Limite de mots pour éviter de dépasser les tokens IA


def _detect_content_type(info: dict) -> ContentType:
    """
    Détecte si le contenu est une vidéo ou un carrousel d'images.
    
    Détection par ordre de priorité:
    1. 'entries' avec plusieurs images (TikTok/playlists)
    2. Instagram carousel indicators (media_type, num_slides, carousel)
    3. '_type' == 'playlist' ou 'multi_video'
    """
    if 'entries' in info and info['entries']:
        entries = info['entries']
        if len(entries) > 1:
            image_extensions = {'jpg', 'jpeg', 'png', 'webp'}
            all_images = all(
                e.get('ext', '').lower() in image_extensions 
                for e in entries if e
            )
            if all_images:
                return ContentType.CAROUSEL
    
    carousel_indicators = [
        info.get('media_type') == 8,
        info.get('num_slides', 0) > 1,
        info.get('carousel_title') is not None,
        info.get('is_unified_collection') == True,
        info.get('_type') in ('playlist', 'multi_video'),
        'carousel' in (info.get('title') or '').lower(),
    ]
    
    if any(carousel_indicators):
        return ContentType.CAROUSEL
    
    return ContentType.VIDEO


def _extract_carousel_image_urls(info: dict) -> list[dict]:
    """
    Extrait les URLs des images depuis les métadonnées yt-dlp.
    Cherche dans plusieurs emplacements possibles.
    """
    image_urls = []
    image_extensions = {'jpg', 'jpeg', 'png', 'webp'}
    
    logger.debug(f"Keys disponibles dans info: {list(info.keys())}")
    
    entries = info.get('entries', [])
    for entry in entries:
        if not entry:
            continue
        url = entry.get('url') or entry.get('thumbnail')
        ext = entry.get('ext', '').lower()
        if url and ext in image_extensions:
            image_urls.append({'url': url, 'ext': ext})
    
    resources = info.get('resources', [])
    for resource in resources:
        if resource.get('type') == 'image':
            url = resource.get('url')
            ext = resource.get('ext', 'jpg').lower()
            if url and ext in image_extensions:
                image_urls.append({'url': url, 'ext': ext})
    
    sidecar_list = info.get('side_data', {}).get('sidecar_thumbnails', [])
    for thumb in sidecar_list:
        url = thumb.get('url')
        if url:
            image_urls.append({'url': url, 'ext': 'jpg'})
    
    if info.get('media_type') == 8:
        thumbnails = info.get('thumbnails', [])
        display_resources = info.get('display_resources', [])
        
        for thumb in thumbnails + display_resources:
            if isinstance(thumb, dict):
                url = thumb.get('url') or thumb.get('src')
                if url:
                    image_urls.append({'url': url, 'ext': 'jpg'})
    
    children = info.get('children', [])
    for child in children:
        if isinstance(child, dict):
            url = child.get('url')
            ext = child.get('ext', 'jpg').lower()
            if url and ext in image_extensions:
                image_urls.append({'url': url, 'ext': ext})
    
    carousel_data = info.get('carousel_parent', {})
    if carousel_data:
        carousel_images = carousel_data.get('image_versions', [])
        for img in carousel_images:
            if isinstance(img, dict):
                url = img.get('url') or img.get('image')
                if url:
                    image_urls.append({'url': url, 'ext': 'jpg'})
    
    candidate_candidates = info.get('candidate', [])
    for cand in candidate_candidates:
        if isinstance(cand, dict):
            url = cand.get('url')
            if url:
                image_urls.append({'url': url, 'ext': 'jpg'})
    
    return image_urls


def _download_carousel_images(
    info: dict,
    output_dir: str,
    max_images: int = MAX_CAROUSEL_IMAGES
) -> tuple[list[str], int]:
    """
    Télécharge les images d'un carrousel Instagram/TikTok.
    Retourne (file_paths, image_count).
    """
    os.makedirs(output_dir, exist_ok=True)
    file_paths: list[str] = []
    
    image_sources = _extract_carousel_image_urls(info)
    
    logger.info(f"URLs d'images extraites : {len(image_sources)}")
    
    for idx, source in enumerate(image_sources[:max_images]):
        url = source.get('url')
        ext = source.get('ext', 'jpg').lower()
        
        if not url:
            continue
        
        output_path = os.path.join(output_dir, f"image_{idx:03d}.{ext}")
        
        import httpx
        try:
            response = httpx.get(url, timeout=30, follow_redirects=True)
            response.raise_for_status()
            with open(output_path, 'wb') as f:
                f.write(response.content)
            
            if os.path.exists(output_path) and os.path.getsize(output_path) > 0:
                file_paths.append(output_path)
                logger.info(f"Image {idx+1} téléchargée : {os.path.getsize(output_path)} octets")
        except Exception as e:
            logger.warning(f"Échec téléchargement image {idx}: {e}")
            continue
    
    return file_paths, len(file_paths)

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


def _download_carousel_instaloader(url: str, output_dir: str) -> tuple[list[str], int]:
    """
    Télécharge les images d'un carrousel Instagram via instaloader.
    Retourne (file_paths, image_count).
    """
    try:
        import instaloader
    except ImportError:
        logger.warning("instaloader non installé, impossible de télécharger le carrousel")
        return [], 0
    
    os.makedirs(output_dir, exist_ok=True)
    file_paths: list[str] = []
    
    try:
        L = instaloader.Instaloader(
            download_pictures=True,
            download_video_thumbnails=False,
            download_geotags=False,
            download_comments=False,
            save_metadata=False,
            dirname_pattern=output_dir,
        )
        
        shortcode = url.split("/p/")[1].split("/")[0] if "/p/" in url else None
        if not shortcode:
            logger.warning("Impossible d'extraire le shortcode de l'URL")
            return [], 0
        
        post = instaloader.Post.from_shortcode(L.context, shortcode)
        
        sidecar_nodes = list(post.get_sidecar_nodes())
        
        if not sidecar_nodes:
            logger.info("Le post n'est pas un carrousel (pas de sidecar nodes)")
            return [], 0
        
        logger.info(f"Téléchargement du carrousel {shortcode} ({len(sidecar_nodes)} images)")
        
        for idx, node in enumerate(sidecar_nodes):
            image_url = getattr(node, 'display_url', None) or getattr(node, 'video_url', None)
            if not image_url:
                logger.warning(f"Node {idx} n'a pas d'URL, tentative de téléchargement direct")
                continue
            ext = 'jpg'
            output_path = os.path.join(output_dir, f"image_{idx:03d}.{ext}")
            
            try:
                import httpx
                response = httpx.get(image_url, timeout=30, follow_redirects=True)
                response.raise_for_status()
                with open(output_path, 'wb') as f:
                    f.write(response.content)
                
                if os.path.exists(output_path) and os.path.getsize(output_path) > 0:
                    file_paths.append(output_path)
                    logger.info(f"Image {idx+1} téléchargée : {os.path.getsize(output_path)} octets")
            except Exception as e:
                logger.warning(f"Échec téléchargement image {idx}: {e}")
                continue
        
        return file_paths, len(file_paths)
        
    except Exception as e:
        logger.warning(f"instaloader a échoué: {e}")
        return [], 0


def _download_instagram_gallery_dl(url: str, output_dir: str) -> tuple[list[str], int]:
    """
    Télécharge les images d'un post Instagram (photo ou carrousel) via gallery-dl.
    Retourne (file_paths, image_count).
    """
    import subprocess
    
    os.makedirs(output_dir, exist_ok=True)
    file_paths: list[str] = []
    
    shortcode = None
    
    patterns = [
        r'/p/([A-Za-z0-9_-]+)',
        r'/reel/([A-Za-z0-9_-]+)',
        r'/reels/([A-Za-z0-9_-]+)',
    ]
    
    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            shortcode = match.group(1)
            break
    
    if not shortcode:
        logger.warning(f"Impossible d'extraire le shortcode de l'URL: {url}")
        return [], 0
    
    logger.info(f"Téléchargement Instagram via gallery-dl : {shortcode}")
    
    try:
        result = subprocess.run(
            [
                "gallery-dl",
                "-q",
                "--cookies-from-browser", "chrome",
                "-D", output_dir,
                url
            ],
            capture_output=True,
            text=True,
            timeout=60,
        )
        
        if result.returncode != 0:
            stderr = result.stderr.strip()
            logger.warning(f"gallery-dl Instagram a échoué: {stderr}")
            return [], 0
        
        downloaded = [f for f in os.listdir(output_dir) if f.endswith(('.jpg', '.jpeg', '.png', '.webp'))]
        downloaded.sort()
        
        for f in downloaded:
            full_path = os.path.join(output_dir, f)
            if os.path.getsize(full_path) > 0:
                file_paths.append(full_path)
                logger.info(f"Image téléchargée : {f}")
        
        if not file_paths:
            logger.warning("gallery-dl n'a pas téléchargé d'images")
            return [], 0
        
        logger.info(f"gallery-dl a téléchargé {len(file_paths)} image(s)")
        return file_paths, len(file_paths)
        
    except FileNotFoundError:
        logger.warning("gallery-dl non installé, retour liste vide")
        return [], 0
    except Exception as e:
        logger.warning(f"Échec gallery-dl Instagram: {e}")
        return [], 0


def _download_tiktok_carousel_yt_dlp(url: str, output_dir: str) -> tuple[list[str], int]:
    """
    Fallback: télécharge les images d'un carrousel TikTok via yt-dlp.
    """
    os.makedirs(output_dir, exist_ok=True)
    file_paths: list[str] = []
    
    try:
        import yt_dlp
        
        ydl_opts = {
            'skip_download': False,
            'format': 'jpg/png/webp',
            'outtmpl': os.path.join(output_dir, 'image_%(autonumber)03d.%(ext)s'),
            'quiet': True,
            'no_warnings': False,
            'logger': _YtdlpLogger(),
        }
        
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            
            if not info:
                return [], 0
            
            formats = info.get('formats', [])
            image_formats = [f for f in formats if f.get('vcodec') == 'none' and f.get('acodec') == 'none']
            
            if not image_formats:
                logger.warning("Pas de formats image trouvés pour ce carrousel")
                return [], 0
            
            for f in sorted(os.listdir(output_dir)):
                if f.startswith('image_') and f.endswith(('.jpg', '.jpeg', '.png', '.webp')):
                    full_path = os.path.join(output_dir, f)
                    if os.path.getsize(full_path) > 0:
                        file_paths.append(full_path)
        
        return file_paths, len(file_paths)
        
    except Exception as e:
        logger.warning(f"Échec yt-dlp pour carrousel TikTok: {e}")
        return [], 0


def _resolve_tiktok_url(url: str) -> str | None:
    """
    Résout une URL TikTok courte (vm.tiktok.com) vers l'URL finale.
    Retourne l'URL finale ou None si échec.
    """
    import httpx
    
    if 'vm.tiktok.com' not in url.lower():
        return url
    
    try:
        response = httpx.head(url, timeout=10, follow_redirects=True, )
        final_url = str(response.url)
        logger.info(f"URL résolue : {url} → {final_url}")
        return final_url
    except Exception as e:
        logger.warning(f"Échec résolution URL TikTok: {e}")
        try:
            response = httpx.get(url, timeout=10, follow_redirects=True, )
            final_url = str(response.url)
            logger.info(f"URL résolue (GET): {url} → {final_url}")
            return final_url
        except Exception as e2:
            logger.warning(f"Échec résolution URL TikTok (GET): {e2}")
            return None


def _download_tiktok_carousel(url: str, output_dir: str) -> tuple[list[str], int]:
    """
    Télécharge les images d'un carrousel TikTok.
    Retourne (file_paths, image_count).
    """
    import httpx
    
    os.makedirs(output_dir, exist_ok=True)
    file_paths: list[str] = []
    
    resolved_url = _resolve_tiktok_url(url)
    if not resolved_url:
        logger.warning("Impossible de résoudre l'URL TikTok")
        return [], 0
    
    photo_id = None
    
    photo_match = re.search(r'photo/(\d+)', resolved_url)
    if photo_match:
        photo_id = photo_match.group(1)
        logger.info(f"Photo ID extrait : {photo_id}")
    
    if not photo_id:
        logger.warning(f"Impossible d'extraire le photo ID de l'URL résolue: {resolved_url}")
        return [], 0
    
    logger.info(f"Téléchargement carrousel TikTok : {photo_id}")
    
    try:
        import subprocess
        
        result = subprocess.run(
            [
                "gallery-dl",
                "-q",
                "--cookies-from-browser", "chrome",
                "-D", output_dir,
                resolved_url or url
            ],
            capture_output=True,
            text=True,
            timeout=60,
        )
        
        if result.returncode != 0:
            stderr = result.stderr.strip()
            logger.warning(f"gallery-dl a échoué: {stderr}")
            if "unsupported url" in stderr.lower():
                logger.info("URL non supportée par gallery-dl, tentative avec yt-dlp...")
                return _download_tiktok_carousel_yt_dlp(resolved_url or url, output_dir)
            return [], 0
        
        downloaded = [f for f in os.listdir(output_dir) if f.endswith(('.jpg', '.jpeg', '.png', '.webp'))]
        downloaded.sort()
        
        for f in downloaded:
            full_path = os.path.join(output_dir, f)
            if os.path.getsize(full_path) > 0:
                file_paths.append(full_path)
                logger.info(f"Image téléchargée : {f}")
        
        if not file_paths:
            logger.warning("gallery-dl n'a pas téléchargé d'images")
            return [], 0
        
        return file_paths, len(file_paths)
        
    except FileNotFoundError:
        logger.warning("gallery-dl non installé. Installez avec: pip install gallery-dl")
        return _download_tiktok_carousel_yt_dlp(resolved_url or url, output_dir)
    except Exception as e:
        logger.warning(f"Échec téléchargement carrousel TikTok: {e}")
        return [], 0


def create_composite_image(image_paths: list[str], output_path: str, max_size: int = 1024) -> str | None:
    """
    Fusionne plusieurs images en une grille pour réduire les appels API.
    
    Args:
        image_paths: Liste des chemins des images à fusionner
        output_path: Chemin de sortie pour l'image composite
        max_size: Taille maximale de chaque image dans la grille (défaut: 1024px)
    
    Returns:
        Chemin de l'image composite ou None si échec
    """
    try:
        from PIL import Image
    except ImportError:
        logger.warning("Pillow non installé, impossible de créer une image composite")
        return None
    
    if not image_paths:
        return None
    
    images = []
    for path in image_paths:
        try:
            img = Image.open(path)
            if img.mode != 'RGB':
                img = img.convert('RGB')
            img.thumbnail((max_size, max_size), Image.Resampling.LANCZOS)
            images.append(img)
        except Exception as e:
            logger.warning(f"Impossible de charger {path}: {e}")
            continue
    
    if not images:
        return None
    
    n = len(images)
    
    if n == 1:
        images[0].save(output_path)
        return output_path
    
    cols = min(3, n)
    rows = (n + cols - 1) // cols
    
    thumb_w = max_size
    thumb_h = max_size
    spacing = 8
    
    total_w = cols * thumb_w + (cols + 1) * spacing
    total_h = rows * thumb_h + (rows + 1) * spacing
    
    composite = Image.new('RGB', (total_w, total_h), (20, 20, 40))
    
    for idx, img in enumerate(images):
        row = idx // cols
        col = idx % cols
        x = spacing + col * (thumb_w + spacing)
        y = spacing + row * (thumb_h + spacing)
        
        img_w, img_h = img.size
        scale = min(thumb_w / img_w, thumb_h / img_h)
        new_w = int(img_w * scale)
        new_h = int(img_h * scale)
        
        img_resized = img.resize((new_w, new_h), Image.Resampling.LANCZOS)
        
        paste_x = x + (thumb_w - new_w) // 2
        paste_y = y + (thumb_h - new_h) // 2
        
        composite.paste(img_resized, (paste_x, paste_y))
    
    composite.save(output_path, 'JPEG', quality=85)
    logger.info(f"Image composite créée : {output_path} ({len(images)} images, {total_w}x{total_h})")
    
    for img in images:
        img.close()
    
    return output_path


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
    """
    Valide l'URL. Accepte TikTok, Instagram, ou tout autre site (traité comme blog).
    """
    url = url.strip()
    m = URL_RE.match(url)
    if not m:
        raise UnsupportedURLError(f"URL malformée : {url!r}")
    domain = m.group("domain").lower()
    
    # Accept TikTok, Instagram, or any other URL (treated as potential blog)
    if domain in SUPPORTED_DOMAINS:
        return url
    
    # Everything else is treated as a blog
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


def _download_with_info(
    url: str,
    output_path: str,
    cookies_file: str | None,
    proxy: str | None,
) -> tuple[dict, bool]:
    """
    Télécharge et retourne les métadonnées yt-dlp pour détection de carrousel.
    Retourne (info_dict, success).
    """
    import json
    import subprocess
    
    logger.info(f"_download_with_info called with URL: {url}")
    
    has_curl = _curl_cffi_available()
    logger.info(f"has_curl: {has_curl}")
    
    strategies = _build_strategies(cookies_file, proxy, has_curl)
    logger.info(f"Nombre de stratégies: {len(strategies)}")
    
    for i, strategy in enumerate(strategies, start=1):
        logger.info(f"Exécution stratégie {i}/{len(strategies)}: {strategy.label}")
        
        if i == 1:
            try:
                cmd = ['yt-dlp', '--dump-json', '--no-playlist', '--socket-timeout', '30', url]
                logger.info(f"Test basique sans impersonation...")
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
                logger.info(f"Test basique returncode: {result.returncode}, stdout len: {len(result.stdout)}, stderr len: {len(result.stderr)}")
                if result.stdout and '{' in result.stdout:
                    info = json.loads(result.stdout.strip().split('\n')[-1])
                    logger.info(f"JSON trouvé avec test basique!")
                    return info, True
            except Exception as e:
                logger.warning(f"Test basique échoué: {e}")
        
        try:
            cmd = ['yt-dlp', '--dump-json', '--no-playlist', 
                   '--socket-timeout', '30']
            
            if strategy.impersonate:
                cmd.extend(['--impersonate', str(strategy.impersonate)])
            if strategy.cookies_from_browser:
                cmd.extend(['--cookies-from-browser', strategy.cookies_from_browser])
            if strategy.cookies_file:
                cmd.extend(['--cookies', strategy.cookies_file])
            if strategy.proxy:
                cmd.extend(['--proxy', strategy.proxy])
            
            cmd.append(url)
            
            logger.info(f"Commande: {cmd[:6]}...")
            
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=30,
            )
            
            logger.info(f"Stratégie {i} returncode: {result.returncode}")
            
            combined = (result.stdout or '') + '\n' + (result.stderr or '')
            
            logger.info(f"Stratégie {i} stdout len: {len(result.stdout or '')}, stderr len: {len(result.stderr or '')}")
            
            if result.returncode == 0 and combined:
                lines = combined.strip().split('\n')
                for line in reversed(lines):
                    line = line.strip()
                    if line and line.startswith('{'):
                        try:
                            info = json.loads(line)
                            logger.info(f"JSON parsed successfully, _type: {info.get('_type')}, entries: {len(info.get('entries', []))}")
                            return info, True
                        except json.JSONDecodeError as e:
                            logger.warning(f"JSON parse error: {e}")
                            continue
                logger.warning(f"Tentative {i}: pas de JSON valide trouvé dans stdout+stderr, combined length: {len(combined)}")
            else:
                stderr = (result.stderr or '').strip()[:500]
                logger.warning(f"Tentative {i} failed with returncode {result.returncode}: {stderr[:200]}")
                
        except subprocess.TimeoutExpired:
            logger.warning(f"Tentative {i} timeout après 30s")
        except Exception as e:
            logger.warning(f"Tentative {i} exception: {e}")
        continue
    
    logger.warning("Toutes les stratégies ont échoué, retour {}, False")
    return {}, False


def is_blog_url(url: str) -> bool:
    """
    Détecte si une URL doit être traitée comme un article de blog.
    Retourne True pour tout URL qui n'est pas TikTok ou Instagram.
    """
    url_lower = url.lower()
    
    # Check if it's TikTok or Instagram - those are not blogs
    for domain in SUPPORTED_DOMAINS:
        if domain in url_lower:
            return False
    
    # Everything else is treated as a blog
    return True


def extract_blog_content(url: str) -> dict:
    """
    Extrait le contenu textuel d'un article de blog.
    Retourne un dict avec title, content, word_count, estimated_read_time.
    """
    import httpx
    from bs4 import BeautifulSoup
    try:
        from readability import Document
    except ImportError:
        logger.warning("readability-lxml non installé, utilisation de BeautifulSoup seul")
        Document = None
    
    logger.info(f"Extraction du contenu blog: {url}")
    
    try:
        response = httpx.get(url, timeout=30, follow_redirects=True, headers={
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        })
        response.raise_for_status()
        html = response.text
        
        title = url
        content = ""
        
        # Try readability first for clean article extraction
        if Document:
            try:
                doc = Document(html)
                title = doc.title() or title
                # Get text content
                soup = BeautifulSoup(doc.summary(), 'html.parser')
                content = soup.get_text(separator='\n', strip=True)
            except Exception as e:
                logger.warning(f"readability extraction failed: {e}, falling back to BeautifulSoup")
                soup = BeautifulSoup(html, 'html.parser')
                # Try to find article content
                article = soup.find('article') or soup.find('main') or soup.find('div', class_=lambda x: x and 'content' in x.lower())
                if article:
                    content = article.get_text(separator='\n', strip=True)
                else:
                    content = soup.get_text(separator='\n', strip=True)
        else:
            soup = BeautifulSoup(html, 'html.parser')
            # Try common article containers
            article = soup.find('article') or soup.find('main') or soup.find('div', class_=lambda x: x and 'content' in x.lower())
            if article:
                content = article.get_text(separator='\n', strip=True)
            else:
                content = soup.get_text(separator='\n', strip=True)
        
        # Clean up content
        lines = [line.strip() for line in content.split('\n') if line.strip()]
        content = '\n'.join(lines)
        
        # Count words
        word_count = len(content.split())
        
        # Limit to MAX_BLOG_WORDS
        if word_count > MAX_BLOG_WORDS:
            words = content.split()
            content = ' '.join(words[:MAX_BLOG_WORDS])
            word_count = MAX_BLOG_WORDS
            logger.info(f"Contenu blog limité à {MAX_BLOG_WORDS} mots")
        
        # Estimate read time (200 words per minute)
        estimated_read_time = max(1, round(word_count / 200))
        
        logger.info(f"Blog extrait: {word_count} mots, ~{estimated_read_time} min de lecture")
        
        return {
            "title": title,
            "content": content,
            "word_count": word_count,
            "estimated_read_time": estimated_read_time,
        }
        
    except httpx.HTTPError as e:
        logger.error(f"HTTP error extracting blog: {e}")
        raise BlogExtractionError(f"Impossible d'accéder à l'article: {e}")
    except Exception as e:
        logger.error(f"Error extracting blog content: {e}")
        raise BlogExtractionError(f"Erreur lors de l'extraction: {e}")


class BlogExtractionError(Exception):
    """Erreur lors de l'extraction du contenu blog"""
    pass


async def download_content(
    url: str,
    output_dir: str,
    cookies_file: str | None = None,
    proxy: str | None = None,
) -> DownloadResult:
    """
    Télécharge une vidéo, un carrousel d'images, ou extrait un article de blog.
    Retourne un DownloadResult avec le type détecté et les chemins de fichiers.
    """
    # Check if it's a blog URL first
    if is_blog_url(url):
        logger.info("[DOWNLOAD_BLOG] Starting blog extraction for: %s", url)
        try:
            loop = asyncio.get_running_loop()
            blog_data = await loop.run_in_executor(None, extract_blog_content, url)
            
            # Save content to a text file
            content_file = os.path.join(output_dir, "content.txt")
            logger.info("[DOWNLOAD_BLOG] Saving content to: %s", content_file)
            with open(content_file, 'w', encoding='utf-8') as f:
                f.write(f"# {blog_data['title']}\n\n")
                f.write(blog_data['content'])
            
            logger.info("[DOWNLOAD_BLOG] Content saved - title: %s, words: %d, read_time: %d min",
                blog_data['title'], blog_data['word_count'], blog_data['estimated_read_time'])
            
            return DownloadResult(
                content_type=ContentType.BLOG,
                file_paths=[content_file],
                word_count=blog_data['word_count'],
                estimated_read_time=blog_data['estimated_read_time'],
            )
        except BlogExtractionError:
            raise
        except Exception as e:
            logger.error(f"Erreur extraction blog: {e}")
            raise BlogExtractionError(f"Erreur lors de l'extraction: {e}")
    
    validated_url = validate_url(url)
    logger.info("Téléchargement de %s", validated_url)
    
    loop = asyncio.get_running_loop()
    video_path = os.path.join(output_dir, "video.mp4")
    os.makedirs(output_dir, exist_ok=True)
    
    is_tiktok_photo = "/photo/" in validated_url.lower() and "tiktok.com" in validated_url.lower()
    
    if is_tiktok_photo:
        logger.info("Détection URL photo TikTok,的直接 carrousel...")
        loop = asyncio.get_running_loop()
        file_paths, image_count = await loop.run_in_executor(
            None,
            partial(_download_tiktok_carousel, validated_url, output_dir)
        )
        
        if file_paths:
            return DownloadResult(
                content_type=ContentType.CAROUSEL,
                file_paths=file_paths,
                duration_seconds=0.0,
                image_count=image_count,
            )
        raise DownloadError("Impossible de télécharger le carrousel TikTok.")
    
    video_downloaded = False
    try:
        await download_video(validated_url, video_path, cookies_file, proxy)
        video_downloaded = True
    except DownloadError as e:
        error_msg = str(e).lower()
        logger.info("download_video a levé DownloadError: %s", e)
        
        is_empty_or_missing = "vide ou introuvable" in error_msg
        is_unsupported = "unsupported url" in error_msg
        is_photo_url = "/photo/" in validated_url.lower()
        is_tiktok = "tiktok.com" in validated_url.lower()
        
        if is_unsupported and (is_photo_url or is_tiktok):
            logger.info("URL TikTok non supportée ou /photo/, tentative carrousel...")
        elif is_empty_or_missing:
            logger.info("Fichier vidéo vide ou inexistant, tentative carrousel...")
        else:
            logger.warning("Erreur inattendue, on propage: %s", e)
            raise
    
    if video_downloaded and os.path.exists(video_path) and os.path.getsize(video_path) > 0:
        duration = 0.0
        try:
            with yt_dlp.YoutubeDL({'quiet': True}) as ydl:
                info = ydl.extract_info(video_path, download=False)
                duration = info.get('duration', 0.0) or 0.0
        except:
            pass
        
        return DownloadResult(
            content_type=ContentType.VIDEO,
            file_paths=[video_path],
            duration_seconds=duration,
            image_count=0,
        )
    
    is_tiktok = "tiktok.com" in validated_url.lower()
    is_instagram_photo = "/p/" in validated_url.lower() and "instagram.com" in validated_url.lower()
    is_instagram_reel = ("/reel/" in validated_url.lower() or "/reels/" in validated_url.lower()) and "instagram.com" in validated_url.lower()
    
    if is_tiktok:
        logger.info("Vidéo vide → tentative carrousel TikTok")
        file_paths, image_count = await loop.run_in_executor(
            None,
            partial(_download_tiktok_carousel, validated_url, output_dir)
        )
        
        if file_paths:
            return DownloadResult(
                content_type=ContentType.CAROUSEL,
                file_paths=file_paths,
                duration_seconds=0.0,
                image_count=image_count,
            )
    
    if is_instagram_photo:
        logger.info("Détection post photo/carrousel Instagram → gallery-dl")
        file_paths, image_count = await loop.run_in_executor(
            None,
            partial(_download_instagram_gallery_dl, validated_url, output_dir)
        )
        
        if not file_paths:
            logger.info("gallery-dl a échoué → tentative instaloader")
            file_paths, image_count = await loop.run_in_executor(
                None,
                partial(_download_carousel_instaloader, validated_url, output_dir)
            )
        
        if file_paths:
            return DownloadResult(
                content_type=ContentType.CAROUSEL,
                file_paths=file_paths,
                duration_seconds=0.0,
                image_count=image_count,
            )
    
    if is_instagram_reel:
        logger.info("Détection Reel Instagram → yt-dlp")
    
    raise DownloadError(
        "Impossible de télécharger ce contenu. "
        "Assurez-vous que le lien est public."
    )