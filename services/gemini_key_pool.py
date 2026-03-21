"""
services/gemini_key_pool.py — Pool de clés API Gemini avec rotation automatique
Bascule automatiquement sur la clé suivante quand une clé atteint son quota (429).
Réinitialise toutes les clés à minuit PST (heure de reset des quotas Google).
"""

import logging
import threading
from datetime import datetime, timezone, timedelta
from typing import Optional

logger = logging.getLogger("bombo.key_pool")

# Fuseau horaire Pacifique (UTC-8, ou UTC-7 en heure d'été)
# On utilise UTC-8 (PST) comme approximation sûre — le reset peut arriver
# 1h plus tôt en PDT, mais on ne sera jamais en retard.
PST = timezone(timedelta(hours=-8))


class AllKeysExhaustedError(Exception):
    """Toutes les clés API sont épuisées pour aujourd'hui."""
    pass


class GeminiKeyPool:
    """
    Pool de clés API Gemini avec rotation automatique.

    Usage :
        pool = GeminiKeyPool(["key1", "key2", "key3"])
        client, idx = pool.get_client()    # → (genai.Client, 0)
        pool.mark_exhausted(idx)            # clé 0 épuisée → passe à 1
        client, idx = pool.get_client()    # → (genai.Client, 1)
    """

    def __init__(self, api_keys: list[str]):
        if not api_keys:
            raise ValueError("Au moins une clé API est requise.")

        self._keys = api_keys
        self._current_index = 0
        self._exhausted: set[int] = set()
        self._lock = threading.Lock()
        self._last_reset_date: str = datetime.now(PST).strftime("%Y-%m-%d")
        self._clients: dict[int, object] = {}  # Cache des clients par index

        logger.info("GeminiKeyPool initialisé avec %d clé(s).", len(self._keys))

    @property
    def total_keys(self) -> int:
        return len(self._keys)

    @property
    def available_keys(self) -> int:
        with self._lock:
            self._check_daily_reset()
            return self.total_keys - len(self._exhausted)

    def get_client(self):
        """
        Retourne (genai.Client, key_index) pour la clé active.
        Lève AllKeysExhaustedError si toutes les clés sont épuisées.
        """
        from google import genai

        with self._lock:
            self._check_daily_reset()

            if len(self._exhausted) >= len(self._keys):
                raise AllKeysExhaustedError(
                    f"Les {len(self._keys)} clé(s) API sont épuisées. "
                    f"Reset à minuit PST (~09h Paris)."
                )

            # Trouver la prochaine clé non épuisée
            idx = self._current_index
            while idx in self._exhausted:
                idx = (idx + 1) % len(self._keys)

            self._current_index = idx

            # Créer le client si pas en cache
            if idx not in self._clients:
                self._clients[idx] = genai.Client(
                    api_key=self._keys[idx]
                )
                logger.debug("Client Gemini créé pour clé #%d", idx + 1)

            return self._clients[idx], idx

    def mark_exhausted(self, key_index: int):
        """Marque une clé comme épuisée et prépare la suivante."""
        with self._lock:
            self._exhausted.add(key_index)

            # Supprimer le client en cache
            self._clients.pop(key_index, None)

            remaining = len(self._keys) - len(self._exhausted)
            logger.warning(
                "Clé #%d épuisée (quota atteint). "
                "Clés restantes : %d/%d",
                key_index + 1,
                remaining,
                len(self._keys),
            )

            if remaining > 0:
                # Passer à la suivante
                self._current_index = (key_index + 1) % len(self._keys)
                while self._current_index in self._exhausted:
                    self._current_index = (self._current_index + 1) % len(self._keys)
                logger.info(
                    "Basculement sur la clé #%d",
                    self._current_index + 1,
                )
            else:
                logger.error(
                    "Toutes les clés sont épuisées ! "
                    "Reset automatique à minuit PST (~09h Paris)."
                )

    def _check_daily_reset(self):
        """
        Réinitialise toutes les clés si on a passé minuit PST.
        Doit être appelé sous lock.
        """
        now_pst = datetime.now(PST)
        today_str = now_pst.strftime("%Y-%m-%d")

        if self._last_reset_date != today_str:
            if self._exhausted:
                logger.info(
                    "Nouveau jour PST (%s) — réinitialisation de %d clé(s) épuisée(s).",
                    today_str,
                    len(self._exhausted),
                )
            self._exhausted.clear()
            self._clients.clear()  # Recréer les clients avec clés fraîches
            self._current_index = 0
            self._last_reset_date = today_str

    def status(self) -> dict:
        """Retourne l'état du pool pour le monitoring / health check."""
        with self._lock:
            self._check_daily_reset()
            return {
                "total_keys": len(self._keys),
                "exhausted_keys": len(self._exhausted),
                "available_keys": len(self._keys) - len(self._exhausted),
                "current_key_index": self._current_index + 1,  # 1-indexed pour lisibilité
                "reset_date_pst": self._last_reset_date,
            }
