"""
services/ml_service.py — Analyse vidéo via Google Gemini API
Remplace le modèle local Qwen2-VL.
Interface publique identique : load_model / run_inference / is_ready / unload_model
"""
import json
import logging
import time
from typing import Tuple, Dict, Optional

from utils.prompts import (
    TRAVEL_PROMPT,
    CONTENT_TYPE_DETECTION_PROMPT,
    CITY_EXTRACTION_PROMPT,
    get_fallback_result,
    get_city_fallback_result,
)

logger = logging.getLogger("bombo.ml_service")


class MLService:
    """Wrapper Gemini API — même interface que l'ancien service Qwen2-VL."""

    def __init__(self):
        self._client = None
        self._model_id: Optional[str] = None
        self.device: Optional[str] = "gemini-api"

    def load_model(self, **kwargs):
        """
        Configure le client Gemini.
        Les anciens paramètres (model_id, max_pixels, fps) sont ignorés si présents.
        La clé et le modèle sont lus depuis config.settings.
        """
        from config import settings
        from google import genai

        if not settings.GEMINI_API_KEY:
            logger.warning("GEMINI_API_KEY non configurée — le service ne sera pas opérationnel.")
            return

        self._client = genai.Client(api_key=settings.GEMINI_API_KEY)
        self._model_id = settings.GEMINI_MODEL_ID
        logger.info("Client Gemini initialisé — modèle : %s ✓", self._model_id)

    def unload_model(self):
        """No-op : pas de modèle en mémoire à décharger."""
        self._client = None
        logger.info("Client Gemini libéré.")

    def is_ready(self) -> bool:
        return self._client is not None and bool(self._model_id)

    def run_inference(self, video_path: str, **kwargs) -> Tuple[Dict, float]:
        """
        Analyse une vidéo via Gemini et retourne (result_dict, durée_secondes).
        Flux :
          1. Upload fichier vidéo vers Gemini File API
          2. Attendre que le fichier soit ACTIVE
          3. Générer le contenu (JSON structuré)
          4. Parser la réponse
          5. Supprimer le fichier uploadé (cleanup)
        """
        if not self.is_ready():
            raise RuntimeError("Le client Gemini n'est pas initialisé.")

        from google.genai import types

        t0 = time.time()
        uploaded_file = None

        try:
            # ── Étape 1 : Upload ──────────────────────────────────────────────
            logger.info("Upload de la vidéo vers Gemini File API : %s", video_path)
            uploaded_file = self._client.files.upload(
                file=video_path,
                config=types.UploadFileConfig(mime_type="video/mp4"),
            )
            logger.info("Fichier uploadé : %s (state=%s)", uploaded_file.name, uploaded_file.state)

            # ── Étape 2 : Attendre ACTIVE ─────────────────────────────────────
            max_wait = 120  # secondes
            waited = 0
            while str(uploaded_file.state) not in ("FileState.ACTIVE", "ACTIVE"):
                if waited >= max_wait:
                    raise RuntimeError("Timeout : le fichier Gemini n'est pas devenu ACTIVE.")
                time.sleep(2)
                waited += 2
                uploaded_file = self._client.files.get(name=uploaded_file.name)
                logger.debug("File state: %s (attendu depuis %ds)", uploaded_file.state, waited)

            logger.info("Fichier ACTIVE après %ds — lancement de l'analyse.", waited)

            # ── Étape 3 : Génération ──────────────────────────────────────────
            t_gen = time.time()
            response = self._client.models.generate_content(
                model=self._model_id,
                contents=[uploaded_file, TRAVEL_PROMPT],
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    temperature=0.1,
                ),
            )
            duration = round(time.time() - t_gen, 2)
            logger.info("Génération terminée en %.2fs", duration)

            # ── Étape 4 : Parse ───────────────────────────────────────────────
            raw_text = response.text or ""
            logger.debug("Réponse brute (%d chars) : %s", len(raw_text), raw_text[:300])
            result = self._parse_json(raw_text)

        finally:
            # ── Étape 5 : Cleanup Gemini ──────────────────────────────────────
            if uploaded_file:
                try:
                    self._client.files.delete(name=uploaded_file.name)
                    logger.debug("Fichier Gemini supprimé : %s", uploaded_file.name)
                except Exception as e:
                    logger.warning("Impossible de supprimer le fichier Gemini : %s", e)

        total_duration = round(time.time() - t0, 2)
        return result, total_duration

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
        """
        if not self.is_ready():
            raise RuntimeError("Le client Gemini n'est pas initialisé.")

        from google.genai import types

        t0 = time.time()
        uploaded_file = None

        try:
            # ── Étape 1 : Upload ──────────────────────────────────────────────
            logger.info("Upload de la vidéo vers Gemini File API : %s", video_path)
            uploaded_file = self._client.files.upload(
                file=video_path,
                config=types.UploadFileConfig(mime_type="video/mp4"),
            )
            logger.info("Fichier uploadé : %s (state=%s)", uploaded_file.name, uploaded_file.state)

            # ── Étape 2 : Attendre ACTIVE ─────────────────────────────────────
            max_wait = 120  # secondes
            waited = 0
            while str(uploaded_file.state) not in ("FileState.ACTIVE", "ACTIVE"):
                if waited >= max_wait:
                    raise RuntimeError("Timeout : le fichier Gemini n'est pas devenu ACTIVE.")
                time.sleep(2)
                waited += 2
                uploaded_file = self._client.files.get(name=uploaded_file.name)
                logger.debug("File state: %s (attendu depuis %ds)", uploaded_file.state, waited)

            logger.info("Fichier ACTIVE après %ds — lancement de l'analyse.", waited)

            # ── Étape 3 : Génération ──────────────────────────────────────────
            t_gen = time.time()
            response = self._client.models.generate_content(
                model=self._model_id,
                contents=[uploaded_file, prompt],
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    temperature=0.1,
                ),
            )
            duration = round(time.time() - t_gen, 2)
            logger.info("Génération terminée en %.2fs", duration)

            # ── Étape 4 : Parse ───────────────────────────────────────────────
            raw_text = response.text or ""
            logger.debug("Réponse brute (%d chars) : %s", len(raw_text), raw_text[:300])
            result = self._parse_json_generic(raw_text, fallback_result)

        finally:
            # ── Étape 5 : Cleanup Gemini ──────────────────────────────────────
            if uploaded_file:
                try:
                    self._client.files.delete(name=uploaded_file.name)
                    logger.debug("Fichier Gemini supprimé : %s", uploaded_file.name)
                except Exception as e:
                    logger.warning("Impossible de supprimer le fichier Gemini : %s", e)

        total_duration = round(time.time() - t0, 2)
        return result, total_duration

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
