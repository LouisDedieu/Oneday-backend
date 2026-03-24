"""
services/ml_service.py — Analyse vidéo via Google Gemini API
Utilise GeminiKeyPool pour la rotation automatique des clés.
Interface publique identique : load_model / run_inference / is_ready / unload_model
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
import time
from typing import TYPE_CHECKING, Tuple, Dict, Optional

from utils.prompts import (
    TRAVEL_PROMPT,
    CONTENT_TYPE_DETECTION_PROMPT,
    CITY_EXTRACTION_PROMPT,
    get_fallback_result,
    get_city_fallback_result,
)

if TYPE_CHECKING:
    from services.gemini_key_pool import GeminiKeyPool

logger = logging.getLogger("bombo.ml_service")


class MLService:
    """Wrapper Gemini API avec rotation automatique des clés."""

    def __init__(self) -> None:
        self._key_pool: Optional[GeminiKeyPool] = None
        self._model_id: Optional[str] = None
        self.device: Optional[str] = "gemini-api"

    def load_model(self, **kwargs):
        """
        Configure le pool de clés Gemini.
        Les anciens paramètres (model_id, max_pixels, fps) sont ignorés si présents.
        La clé et le modèle sont lus depuis config.settings.
        """
        from config import settings
        from services.gemini_key_pool import GeminiKeyPool

        keys = settings.gemini_api_key_list
        if not keys:
            logger.warning("Aucune clé Gemini configurée — le service ne sera pas opérationnel.")
            return

        self._key_pool = GeminiKeyPool(keys)
        self._model_id = settings.GEMINI_MODEL_ID
        logger.info(
            "Client Gemini initialisé — modèle : %s, %d clé(s) dans le pool ✓",
            self._model_id,
            len(keys),
        )

    def unload_model(self):
        """No-op : pas de modèle en mémoire à décharger."""
        self._key_pool = None
        logger.info("Client Gemini libéré.")

    def is_ready(self) -> bool:
        return self._key_pool is not None and bool(self._model_id)

    def _call_gemini(self, contents: list, config_obj) -> str:
        """
        Appelle Gemini avec rotation automatique des clés.
        En cas d'erreur 429 (quota), bascule sur la clé suivante et retry.
        Retourne le texte brut de la réponse.
        """
        from services.gemini_key_pool import AllKeysExhaustedError

        last_error = None

        # On peut tenter autant de fois qu'il y a de clés
        for attempt in range(self._key_pool.total_keys):
            client, key_idx = self._key_pool.get_client()

            try:
                response = client.models.generate_content(
                    model=self._model_id,
                    contents=contents,
                    config=config_obj,
                )
                return response.text or ""

            except Exception as e:
                error_str = str(e).lower()
                # Détecter les erreurs de quota (429 / ResourceExhausted)
                if "429" in error_str or "resource" in error_str and "exhausted" in error_str:
                    logger.warning(
                        "Clé #%d : quota atteint (tentative %d/%d) — %s",
                        key_idx + 1,
                        attempt + 1,
                        self._key_pool.total_keys,
                        e,
                    )
                    self._key_pool.mark_exhausted(key_idx)
                    last_error = e
                else:
                    # Autre erreur (réseau, etc.) → ne pas consommer la clé
                    raise

        # Toutes les clés ont été essayées
        raise AllKeysExhaustedError(
            f"Toutes les {self._key_pool.total_keys} clé(s) sont épuisées. "
            f"Dernière erreur : {last_error}"
        )

    def _upload_and_wait(self, client, video_path: str):
        """Upload une vidéo vers Gemini File API et attend qu'elle soit ACTIVE."""
        from google.genai import types

        logger.info("Upload de la vidéo vers Gemini File API : %s", video_path)
        uploaded_file = client.files.upload(
            file=video_path,
            config=types.UploadFileConfig(mime_type="video/mp4"),
        )
        logger.info("Fichier uploadé : %s (state=%s)", uploaded_file.name, uploaded_file.state)

        # Attendre ACTIVE
        max_wait = 120
        waited = 0
        while str(uploaded_file.state) not in ("FileState.ACTIVE", "ACTIVE"):
            if waited >= max_wait:
                raise RuntimeError("Timeout : le fichier Gemini n'est pas devenu ACTIVE.")
            time.sleep(2)
            waited += 2
            uploaded_file = client.files.get(name=uploaded_file.name)
            logger.debug("File state: %s (attendu depuis %ds)", uploaded_file.state, waited)

        logger.info("Fichier ACTIVE après %ds — lancement de l'analyse.", waited)
        return uploaded_file

    def run_inference(self, video_path: str, **kwargs) -> Tuple[Dict, float]:
        """
        Analyse une vidéo via Gemini et retourne (result_dict, durée_secondes).
        Utilise la rotation de clés automatique.
        """
        if not self.is_ready():
            raise RuntimeError("Le client Gemini n'est pas initialisé.")

        from google.genai import types
        from services.gemini_key_pool import AllKeysExhaustedError

        t0 = time.time()
        uploaded_file = None
        client = None

        last_error = None

        for attempt in range(self._key_pool.total_keys):
            client, key_idx = self._key_pool.get_client()

            try:
                # ── Étape 1-2 : Upload + wait ─────────────────────────────────
                uploaded_file = self._upload_and_wait(client, video_path)

                # ── Étape 3 : Génération ──────────────────────────────────────
                t_gen = time.time()
                response = client.models.generate_content(
                    model=self._model_id,
                    contents=[uploaded_file, TRAVEL_PROMPT],
                    config=types.GenerateContentConfig(
                        response_mime_type="application/json",
                        temperature=0.1,
                    ),
                )
                duration = round(time.time() - t_gen, 2)
                logger.info("Génération terminée en %.2fs", duration)

                # ── Étape 4 : Parse ───────────────────────────────────────────
                raw_text = response.text or ""
                logger.debug("Réponse brute (%d chars) : %s", len(raw_text), raw_text[:300])
                result = self._parse_json(raw_text)

                # ── Cleanup ───────────────────────────────────────────────────
                self._cleanup_file(client, uploaded_file)

                total_duration = round(time.time() - t0, 2)
                return result, total_duration

            except Exception as e:
                error_str = str(e).lower()
                if "429" in error_str or ("resource" in error_str and "exhausted" in error_str):
                    logger.warning(
                        "Clé #%d : quota atteint (tentative %d/%d)",
                        key_idx + 1, attempt + 1, self._key_pool.total_keys,
                    )
                    self._key_pool.mark_exhausted(key_idx)
                    # Cleanup le fichier uploadé avec la clé actuelle avant de retry
                    if uploaded_file:
                        self._cleanup_file(client, uploaded_file)
                        uploaded_file = None
                    last_error = e
                else:
                    # Autre erreur → cleanup et raise
                    if uploaded_file:
                        self._cleanup_file(client, uploaded_file)
                    raise

        raise AllKeysExhaustedError(
            f"Toutes les {self._key_pool.total_keys} clé(s) épuisées. Dernière erreur : {last_error}"
        )

    def run_inference_with_prompt(
        self,
        video_path: str,
        prompt: str,
        fallback_result: Dict,
        **kwargs
    ) -> Tuple[Dict, float]:
        """
        Analyse une vidéo via Gemini avec un prompt personnalisé.
        Utilisé pour la détection du type de contenu et l'extraction city.
        Utilise la rotation de clés automatique.
        """
        if not self.is_ready():
            raise RuntimeError("Le client Gemini n'est pas initialisé.")

        from google.genai import types
        from services.gemini_key_pool import AllKeysExhaustedError

        t0 = time.time()
        uploaded_file = None
        client = None
        last_error = None

        for attempt in range(self._key_pool.total_keys):
            client, key_idx = self._key_pool.get_client()

            try:
                # ── Upload + wait ─────────────────────────────────────────────
                uploaded_file = self._upload_and_wait(client, video_path)

                # ── Génération ────────────────────────────────────────────────
                t_gen = time.time()
                response = client.models.generate_content(
                    model=self._model_id,
                    contents=[uploaded_file, prompt],
                    config=types.GenerateContentConfig(
                        response_mime_type="application/json",
                        temperature=0.1,
                    ),
                )
                duration = round(time.time() - t_gen, 2)
                logger.info("Génération terminée en %.2fs", duration)

                # ── Parse ─────────────────────────────────────────────────────
                raw_text = response.text or ""
                logger.debug("Réponse brute (%d chars) : %s", len(raw_text), raw_text[:300])
                result = self._parse_json_generic(raw_text, fallback_result)

                # ── Cleanup ───────────────────────────────────────────────────
                self._cleanup_file(client, uploaded_file)

                total_duration = round(time.time() - t0, 2)
                return result, total_duration

            except Exception as e:
                error_str = str(e).lower()
                if "429" in error_str or ("resource" in error_str and "exhausted" in error_str):
                    logger.warning(
                        "Clé #%d : quota atteint (tentative %d/%d)",
                        key_idx + 1, attempt + 1, self._key_pool.total_keys,
                    )
                    self._key_pool.mark_exhausted(key_idx)
                    if uploaded_file:
                        self._cleanup_file(client, uploaded_file)
                        uploaded_file = None
                    last_error = e
                else:
                    if uploaded_file:
                        self._cleanup_file(client, uploaded_file)
                    raise

        raise AllKeysExhaustedError(
            f"Toutes les {self._key_pool.total_keys} clé(s) épuisées. Dernière erreur : {last_error}"
        )

    def detect_entity_type(self, video_path: str) -> str:
        """
        Détecte si la vidéo est un trip ou une city guide.
        Retourne 'trip' ou 'city'.
        """
        fallback = {"entity_type": "trip"}
        result, _ = self.run_inference_with_prompt(
            video_path,
            CONTENT_TYPE_DETECTION_PROMPT,
            fallback,
        )
        entity_type = result.get("entity_type", "trip")
        if entity_type not in ("trip", "city"):
            entity_type = "trip"
        logger.info("Type de contenu détecté : %s", entity_type)
        return entity_type

    def run_city_inference(self, video_path: str, **kwargs) -> Tuple[Dict, float]:
        """
        Analyse une vidéo comme city guide.
        """
        return self.run_inference_with_prompt(
            video_path,
            CITY_EXTRACTION_PROMPT,
            get_city_fallback_result(),
        )

    def run_inference_from_images(self, image_paths: list[str], **kwargs) -> Tuple[Dict, float]:
        """
        Analyse une séquence d'images (carrousel) via Gemini.
        Utilise une image composite pour réduire les appels API.
        Avec retry sur les erreurs serveur (500, 503).
        """
        if not self.is_ready():
            raise RuntimeError("Le client Gemini n'est pas initialisé.")

        if not image_paths:
            raise ValueError("Aucune image à analyser.")

        from google.genai import types
        from services.gemini_key_pool import AllKeysExhaustedError
        from downloader import create_composite_image

        t0 = time.time()
        last_error = None
        max_retries = 3
        retry_delay = 5

        composite_path = None
        uploaded_file = None

        for retry in range(max_retries):
            client = None

            for key_attempt in range(self._key_pool.total_keys):
                client, key_idx = self._key_pool.get_client()

                try:
                    if composite_path is None:
                        if len(image_paths) == 1:
                            logger.info("[carousel] 1 seule image, upload direct")
                            uploaded_file = self._upload_image(client, image_paths[0])
                            logger.info("[carousel] Upload direct (tentative %d, clé #%d)",
                                        retry + 1, key_idx + 1)
                            contents = [uploaded_file, TRAVEL_PROMPT]
                        else:
                            logger.info("[carousel] Création de l'image composite (%d images)...", len(image_paths))
                            composite_path = os.path.join(tempfile.gettempdir(), f"carousel_composite_{int(time.time() * 1000)}.jpg")
                            result_composite = create_composite_image(image_paths, composite_path)
                            if not result_composite:
                                raise RuntimeError("Échec de la création de l'image composite")
                            logger.info("[carousel] Image composite créée : %s", composite_path)
                            uploaded_file = self._upload_image(client, composite_path)
                            logger.info("[carousel] Upload de l'image composite (tentative %d, clé #%d)",
                                        retry + 1, key_idx + 1)
                            contents = [uploaded_file, TRAVEL_PROMPT]
                    else:
                        uploaded_file = self._upload_image(client, composite_path)
                        contents = [uploaded_file, TRAVEL_PROMPT]

                    logger.info("[carousel] Lancement de l'analyse Gemini...")
                    t_gen = time.time()
                    response = client.models.generate_content(
                        model=self._model_id,
                        contents=contents,
                        config=types.GenerateContentConfig(
                            response_mime_type="application/json",
                            temperature=0.1,
                        ),
                    )
                    duration = round(time.time() - t_gen, 2)
                    logger.info("[carousel] Génération terminée en %.2fs", duration)

                    raw_text = response.text or ""
                    result = self._parse_json(raw_text)

                    if uploaded_file:
                        self._cleanup_file(client, uploaded_file)
                    if composite_path and os.path.exists(composite_path):
                        try:
                            os.remove(composite_path)
                        except Exception:
                            pass

                    total_duration = round(time.time() - t0, 2)
                    return result, total_duration

                except Exception as e:
                    error_str = str(e).lower()
                    is_quota = "429" in error_str or ("resource" in error_str and "exhausted" in error_str)
                    is_server_error = any(code in error_str for code in ["500", "503", "internal", "server error"])

                    if is_quota:
                        logger.warning("[carousel] Clé #%d : quota atteint", key_idx + 1)
                        self._key_pool.mark_exhausted(key_idx)
                        if uploaded_file:
                            self._cleanup_file(client, uploaded_file)
                        uploaded_file = None
                        last_error = e
                    elif is_server_error:
                        logger.warning("[carousel] Erreur serveur Gemini (%s) - retry %d/%d",
                                       str(e), retry + 1, max_retries)
                        if uploaded_file:
                            self._cleanup_file(client, uploaded_file)
                        uploaded_file = None
                        last_error = e
                        break
                    else:
                        if uploaded_file:
                            self._cleanup_file(client, uploaded_file)
                        raise

            if last_error and "500" not in str(last_error).lower():
                break

            if retry < max_retries - 1:
                logger.info("[carousel] Pause de %ds avant retry...", retry_delay)
                time.sleep(retry_delay)

        raise AllKeysExhaustedError(
            f"Toutes les tentatives épuisées après {max_retries} retries. "
            f"Dernière erreur : {last_error}"
        )

    def run_city_inference_from_images(self, image_paths: list[str], **kwargs) -> Tuple[Dict, float]:
        """
        Analyse une séquence d'images (carrousel) comme city guide.
        Avec retry sur les erreurs serveur (500, 503).
        """
        if not self.is_ready():
            raise RuntimeError("Le client Gemini n'est pas initialisé.")

        if not image_paths:
            raise ValueError("Aucune image à analyser.")

        from google.genai import types
        from services.gemini_key_pool import AllKeysExhaustedError
        from downloader import create_composite_image

        t0 = time.time()
        last_error = None
        max_retries = 3
        retry_delay = 5

        composite_path = None
        uploaded_file = None

        for retry in range(max_retries):
            client = None

            for key_attempt in range(self._key_pool.total_keys):
                client, key_idx = self._key_pool.get_client()

                try:
                    if composite_path is None:
                        if len(image_paths) == 1:
                            logger.info("[carousel] 1 seule image, upload direct")
                            uploaded_file = self._upload_image(client, image_paths[0])
                            logger.info("[carousel] Upload direct (tentative %d, clé #%d)",
                                        retry + 1, key_idx + 1)
                            contents = [uploaded_file, CITY_EXTRACTION_PROMPT]
                            file_to_upload = None
                        else:
                            logger.info("[carousel] Création de l'image composite (%d images)...", len(image_paths))
                            import tempfile
                            composite_path = os.path.join(tempfile.gettempdir(), f"carousel_composite_{int(time.time() * 1000)}.jpg")
                            result_composite = create_composite_image(image_paths, composite_path)
                            if not result_composite:
                                raise RuntimeError("Échec de la création de l'image composite")
                            logger.info("[carousel] Image composite créée : %s", composite_path)
                            uploaded_file = self._upload_image(client, composite_path)
                            logger.info("[carousel] Upload de l'image composite (tentative %d, clé #%d)",
                                        retry + 1, key_idx + 1)
                            contents = [uploaded_file, CITY_EXTRACTION_PROMPT]
                            file_to_upload = None
                    else:
                        uploaded_file = self._upload_image(client, composite_path)
                        contents = [uploaded_file, CITY_EXTRACTION_PROMPT]
                        file_to_upload = None

                    logger.info("[carousel] Lancement de l'analyse Gemini...")
                    t_gen = time.time()
                    response = client.models.generate_content(
                        model=self._model_id,
                        contents=contents,
                        config=types.GenerateContentConfig(
                            response_mime_type="application/json",
                            temperature=0.1,
                        ),
                    )
                    duration = round(time.time() - t_gen, 2)
                    logger.info("[carousel] Génération terminée en %.2fs", duration)

                    raw_text = response.text or ""
                    result = self._parse_json_generic(raw_text, get_city_fallback_result())

                    if uploaded_file:
                        self._cleanup_file(client, uploaded_file)
                    if composite_path and os.path.exists(composite_path):
                        try:
                            os.remove(composite_path)
                        except Exception:
                            pass

                    total_duration = round(time.time() - t0, 2)
                    return result, total_duration

                except Exception as e:
                    error_str = str(e).lower()
                    is_quota = "429" in error_str or ("resource" in error_str and "exhausted" in error_str)
                    is_server_error = any(code in error_str for code in ["500", "503", "internal", "server error"])

                    if is_quota:
                        logger.warning("[carousel] Clé #%d : quota atteint", key_idx + 1)
                        self._key_pool.mark_exhausted(key_idx)
                        if uploaded_file:
                            self._cleanup_file(client, uploaded_file)
                        uploaded_file = None
                        last_error = e
                    elif is_server_error:
                        logger.warning("[carousel] Erreur serveur Gemini (%s) - retry %d/%d",
                                       str(e), retry + 1, max_retries)
                        if uploaded_file:
                            self._cleanup_file(client, uploaded_file)
                        uploaded_file = None
                        last_error = e
                        break
                    else:
                        if uploaded_file:
                            self._cleanup_file(client, uploaded_file)
                        raise

            if last_error and "500" not in str(last_error).lower():
                break

            if retry < max_retries - 1:
                logger.info("[carousel] Pause de %ds avant retry...", retry_delay)
                time.sleep(retry_delay)

        raise AllKeysExhaustedError(
            f"Toutes les tentatives épuisées après {max_retries} retries. "
            f"Dernière erreur : {last_error}"
        )

    def _upload_image(self, client, image_path: str):
        """Upload une image vers Gemini File API et attend qu'elle soit ACTIVE."""
        import mimetypes
        from google.genai import types as genai_types

        mime_type = mimetypes.guess_type(image_path)[0] or "image/jpeg"
        logger.info("Upload de l'image vers Gemini File API : %s", image_path)

        uploaded_file = client.files.upload(
            file=image_path,
            config=genai_types.UploadFileConfig(mime_type=mime_type),
        )
        logger.info("Fichier uploadé : %s (state=%s)", uploaded_file.name, uploaded_file.state)

        # Attendre ACTIVE
        max_wait = 60
        waited = 0
        while str(uploaded_file.state) not in ("FileState.ACTIVE", "ACTIVE"):
            if waited >= max_wait:
                raise RuntimeError("Timeout : le fichier Gemini n'est pas devenu ACTIVE.")
            time.sleep(1)
            waited += 1
            uploaded_file = client.files.get(name=uploaded_file.name)
            logger.debug("File state: %s (attendu depuis %ds)", uploaded_file.state, waited)

        logger.info("Image ACTIVE après %ds — prête pour l'analyse.", waited)
        return uploaded_file

    @staticmethod
    def _cleanup_file(client, uploaded_file):
        """Supprime un fichier uploadé sur Gemini."""
        if uploaded_file:
            try:
                client.files.delete(name=uploaded_file.name)
                logger.debug("Fichier Gemini supprimé : %s", uploaded_file.name)
            except Exception as e:
                logger.warning("Impossible de supprimer le fichier Gemini : %s", e)

    def _parse_json_generic(self, raw_text: str, fallback_result: Dict) -> Dict:
        """Parse le JSON de la réponse Gemini avec fallback personnalisable."""
        text = raw_text.strip()
        # Nettoyer les balises markdown au cas où
        if text.startswith("```"):
            text = text.split("\n", 1)[-1]
            text = text.rsplit("```", 1)[0].strip()

        try:
            return json.loads(text)
        except json.JSONDecodeError as e:
            logger.warning("json.loads échoué (%s) — tentative json-repair…", e)

        try:
            from json_repair import repair_json
            repaired = repair_json(text, return_objects=True)
            if isinstance(repaired, dict):
                logger.info("JSON réparé avec succès via json-repair.")
                return repaired
        except Exception as e:
            logger.warning("json-repair échoué : %s", e)

        logger.error("Impossible de parser la réponse Gemini. Retour fallback.")
        return fallback_result

    def _parse_json(self, raw_text: str) -> Dict:
        """Parse le JSON de la réponse Gemini avec fallback json-repair."""
        text = raw_text.strip()
        # Nettoyer les balises markdown au cas où
        if text.startswith("```"):
            text = text.split("\n", 1)[-1]
            text = text.rsplit("```", 1)[0].strip()

        try:
            return json.loads(text)
        except json.JSONDecodeError as e:
            logger.warning("json.loads échoué (%s) — tentative json-repair…", e)

        try:
            from json_repair import repair_json
            repaired = repair_json(text, return_objects=True)
            if isinstance(repaired, dict) and "trip_title" in repaired:
                logger.info("JSON réparé avec succès via json-repair.")
                return repaired
        except Exception as e:
            logger.warning("json-repair échoué : %s", e)

        logger.error("Impossible de parser la réponse Gemini. Retour fallback.")
        return get_fallback_result()


# Instance singleton — même pattern que l'ancien service
ml_service = MLService()
