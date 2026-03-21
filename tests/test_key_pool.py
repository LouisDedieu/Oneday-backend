"""
Tests unitaires pour GeminiKeyPool — rotation automatique des clés API Gemini.
Lancer : pytest tests/test_key_pool.py -v
"""
import pytest
from unittest.mock import patch, MagicMock
from datetime import datetime, timezone, timedelta

from services.gemini_key_pool import GeminiKeyPool, AllKeysExhaustedError, PST


# ── Helpers ───────────────────────────────────────────────────────────────────

def make_pool(n: int = 3) -> GeminiKeyPool:
    """Crée un pool de test avec n fausses clés."""
    keys = [f"fake-key-{i}" for i in range(n)]
    with patch("services.gemini_key_pool.logger"):
        return GeminiKeyPool(keys)


# ── Tests d'initialisation ────────────────────────────────────────────────────

def test_init_with_keys():
    pool = make_pool(3)
    assert pool.total_keys == 3
    assert pool.available_keys == 3


def test_init_empty_raises():
    with pytest.raises(ValueError, match="Au moins une clé"):
        GeminiKeyPool([])


# ── Tests de rotation ─────────────────────────────────────────────────────────

@patch("google.genai.Client")
def test_get_client_returns_first_key(mock_genai_client):
    pool = make_pool(3)
    client, idx = pool.get_client()
    assert idx == 0
    mock_genai_client.assert_called_once_with(api_key="fake-key-0")


@patch("google.genai.Client")
def test_rotation_after_exhaustion(mock_genai_client):
    pool = make_pool(3)

    # Utiliser puis épuiser la clé 0
    _, idx0 = pool.get_client()
    assert idx0 == 0
    pool.mark_exhausted(0)

    # La suivante doit être la clé 1
    mock_genai_client.reset_mock()
    _, idx1 = pool.get_client()
    assert idx1 == 1


@patch("google.genai.Client")
def test_rotation_skips_exhausted(mock_genai_client):
    pool = make_pool(3)

    pool.mark_exhausted(0)
    pool.mark_exhausted(1)

    _, idx = pool.get_client()
    assert idx == 2


@patch("google.genai.Client")
def test_all_keys_exhausted_raises(mock_genai_client):
    pool = make_pool(2)
    pool.mark_exhausted(0)
    pool.mark_exhausted(1)

    with pytest.raises(AllKeysExhaustedError, match="2 clé"):
        pool.get_client()


# ── Tests de reset quotidien ─────────────────────────────────────────────────

@patch("google.genai.Client")
def test_daily_reset_clears_exhausted(mock_genai_client):
    pool = make_pool(2)

    # Simuler : on est "hier" en PST
    yesterday_pst = datetime.now(PST) - timedelta(days=1)
    pool._last_reset_date = yesterday_pst.strftime("%Y-%m-%d")
    pool._exhausted = {0, 1}

    # Appeler get_client devrait déclencher un reset
    _, idx = pool.get_client()
    assert idx == 0
    assert pool.available_keys == 2


@patch("google.genai.Client")
def test_no_reset_same_day(mock_genai_client):
    pool = make_pool(2)

    # Marquer comme le jour actuel en PST
    today_pst = datetime.now(PST).strftime("%Y-%m-%d")
    pool._last_reset_date = today_pst
    pool._exhausted = {0}

    _, idx = pool.get_client()
    assert idx == 1  # Clé 0 toujours épuisée
    assert pool.available_keys == 1


# ── Tests du statut ───────────────────────────────────────────────────────────

@patch("google.genai.Client")
def test_status_returns_correct_info(mock_genai_client):
    pool = make_pool(3)
    pool.get_client()  # Initialiser
    pool.mark_exhausted(0)

    status = pool.status()
    assert status["total_keys"] == 3
    assert status["exhausted_keys"] == 1
    assert status["available_keys"] == 2
    assert status["current_key_index"] == 2  # 1-indexed


# ── Tests de rétro-compatibilité config ───────────────────────────────────────

def test_config_single_key_compat():
    """GEMINI_API_KEY (singulier) est toujours supporté."""
    from config import Settings

    with patch.dict("os.environ", {
        "GEMINI_API_KEY": "single-key",
        "GEMINI_API_KEYS": "",
    }):
        s = Settings()
        keys = s.gemini_api_key_list
        assert keys == ["single-key"]


def test_config_multi_keys():
    """GEMINI_API_KEYS (pluriel) fonctionne avec des virgules."""
    from config import Settings

    with patch.dict("os.environ", {
        "GEMINI_API_KEY": "",
        "GEMINI_API_KEYS": "key-a, key-b, key-c",
    }):
        s = Settings()
        keys = s.gemini_api_key_list
        assert keys == ["key-a", "key-b", "key-c"]


def test_config_multi_keys_takes_priority():
    """GEMINI_API_KEYS a priorité sur GEMINI_API_KEY."""
    from config import Settings

    with patch.dict("os.environ", {
        "GEMINI_API_KEY": "old-single",
        "GEMINI_API_KEYS": "new-a,new-b",
    }):
        s = Settings()
        keys = s.gemini_api_key_list
        assert keys == ["new-a", "new-b"]
