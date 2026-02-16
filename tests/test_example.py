"""
Exemples de tests unitaires pour les services refactorisés
Installer pytest : pip install pytest pytest-asyncio
Lancer : pytest tests/
"""
import pytest
from unittest.mock import Mock, AsyncMock, patch
import asyncio

# ── Tests pour MLService ──────────────────────────────────────────────────────

def test_ml_service_initialization():
    """Test que MLService s'initialise correctement"""
    from services.ml_service import MLService

    service = MLService()
    assert service.model is None
    assert service.processor is None
    assert service.device is None
    assert not service.is_ready()


def test_ml_service_is_ready():
    """Test la méthode is_ready()"""
    from services.ml_service import MLService

    service = MLService()
    assert not service.is_ready()

    # Simuler le chargement
    service.model = Mock()
    service.processor = Mock()
    assert service.is_ready()


# ── Tests pour JobManager ─────────────────────────────────────────────────────

def test_job_manager_create_job():
    """Test la création d'un job"""
    from services.sse_service import JobManager

    manager = JobManager()
    job_id = "test-job-123"

    manager.create_job(job_id)

    assert manager.job_exists(job_id)
    job = manager.get_job(job_id)
    assert job["status"] == "pending"
    assert job["result"] is None
    assert job["error"] is None
    assert job["sse_queues"] == []


def test_job_manager_update_status():
    """Test la mise à jour du statut d'un job"""
    from services.sse_service import JobManager

    manager = JobManager()
    job_id = "test-job-456"

    manager.create_job(job_id)
    manager.update_job_status(job_id, "processing", progress=50)

    job = manager.get_job(job_id)
    assert job["status"] == "processing"
    assert job["progress"] == 50


@pytest.mark.asyncio
async def test_job_manager_send_sse_update():
    """Test l'envoi de mises à jour SSE"""
    from services.sse_service import JobManager

    manager = JobManager()
    job_id = "test-job-789"

    manager.create_job(job_id)

    # Créer une queue de test
    queue = asyncio.Queue()
    manager.add_sse_queue(job_id, queue)

    # Envoyer une mise à jour
    await manager.send_sse_update(job_id, "processing", {"progress": 25})

    # Vérifier que le message a été reçu
    message = await queue.get()
    assert message["job_id"] == job_id
    assert message["status"] == "processing"
    assert message["progress"] == 25
    assert "timestamp" in message


# ── Tests pour SupabaseService ────────────────────────────────────────────────

def test_supabase_service_initialization():
    """Test l'initialisation du service Supabase"""
    from services.supabase_service import SupabaseService

    service = SupabaseService(url="https://test.supabase.co", key="test-key")
    assert service.url == "https://test.supabase.co"
    assert service.key == "test-key"
    assert service.is_configured()


def test_supabase_service_not_configured():
    """Test quand Supabase n'est pas configuré"""
    from services.supabase_service import SupabaseService

    service = SupabaseService(url=None, key=None)
    assert not service.is_configured()


def test_supabase_service_normalize_season():
    """Test la normalisation des saisons"""
    from services.supabase_service import SupabaseService

    service = SupabaseService()
    service.season_enum_values = {"spring", "summer", "autumn", "winter"}

    # Test exact match
    assert service.normalize_season("summer") == "summer"

    # Test case-insensitive
    assert service.normalize_season("SUMMER") == "summer"

    # Test partial match
    assert service.normalize_season("late summer") == "summer"

    # Test no match
    assert service.normalize_season("unknown") is None


# ── Tests pour les schémas Pydantic ───────────────────────────────────────────

def test_analyze_url_request_validation():
    """Test la validation des requêtes d'analyse"""
    from models.schemas import AnalyzeUrlRequest
    from pydantic import ValidationError

    # URL valide
    request = AnalyzeUrlRequest(url="https://www.tiktok.com/@user/video/123")
    assert request.url.startswith("https://")

    # URL invalide (sans http)
    with pytest.raises(ValidationError):
        AnalyzeUrlRequest(url="www.tiktok.com/@user/video/123")


def test_job_response_schema():
    """Test le schéma de réponse des jobs"""
    from models.schemas import JobResponse

    response = JobResponse(job_id="test-123")
    assert response.job_id == "test-123"


# ── Tests d'intégration (exemple) ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_job_processor_error_handling():
    """Test la gestion d'erreurs dans JobProcessor.

    FIX : on injecte le même JobManager dans JobProcessor plutôt que de
    laisser le processor en créer un en interne. Sans injection, les deux
    instances sont indépendantes et le test lit toujours 'pending'.
    """
    from services.job_processor import JobProcessor
    from services.supabase_service import SupabaseService
    from services.sse_service import JobManager
    from models.schemas import AnalyzeUrlRequest

    # Setup — on partage le même manager entre le test et le processor
    supabase = SupabaseService()  # Non configuré
    manager = JobManager()
    processor = JobProcessor(supabase, manager)  # ← injection du manager partagé

    job_id = "test-error-job"
    manager.create_job(job_id)

    # Créer une requête avec une URL invalide
    request = AnalyzeUrlRequest(url="https://invalid-platform.com/video/123")

    # Le job devrait échouer avec une erreur
    await processor.process_url_job(job_id, request)

    job = manager.get_job(job_id)
    assert job["status"] == "error"
    assert job["error"] is not None


# ── Tests de mock (exemple avancé) ────────────────────────────────────────────

@pytest.mark.asyncio
async def test_supabase_service_create_trip_with_mock():
    """Test la création d'un trip avec mock de httpx"""
    from services.supabase_service import SupabaseService

    service = SupabaseService(
        url="https://test.supabase.co",
        key="test-key"
    )

    trip_data = {
        "trip_title": "Test Trip",
        "vibe": "adventure",
        "duration_days": 5,
        "destinations": [],
        "itinerary": [],
        "logistics": [],
        "budget": {},
        "practical_info": {},
        "content_creator": {},
    }

    # Mock httpx pour éviter les vraies requêtes
    with patch('httpx.AsyncClient') as mock_client:
        # Configurer le mock pour retourner un trip_id
        mock_response = Mock()
        mock_response.json.return_value = [{"id": "trip-123"}]
        mock_response.raise_for_status = Mock()

        mock_client.return_value.__aenter__.return_value.post = AsyncMock(
            return_value=mock_response
        )

        # Note : ce test nécessiterait plus de configuration pour fonctionner
        # car create_trip utilise un thread synchrone avec httpx synchrone
        # C'est un exemple de structure de test


# ── Fixtures pytest (utilitaires de test) ─────────────────────────────────────

@pytest.fixture
def sample_trip_data():
    """Fixture fournissant des données de trip de test"""
    return {
        "trip_title": "Amazing Japan Trip",
        "vibe": "cultural",
        "duration_days": 7,
        "best_season": "spring",
        "destinations": [
            {
                "city": "Tokyo",
                "country": "Japan",
                "days_spent": 4,
                "order": 1
            },
            {
                "city": "Kyoto",
                "country": "Japan",
                "days_spent": 3,
                "order": 2
            }
        ],
        "itinerary": [],
        "logistics": [],
        "budget": {
            "total_estimated": 2000,
            "currency": "EUR",
            "per_day": {"min": 200, "max": 300},
            "breakdown": {}
        },
        "practical_info": {},
        "content_creator": {
            "handle": "@traveler",
            "links_mentioned": []
        }
    }


@pytest.fixture
def mock_ml_service():
    """Fixture fournissant un service ML mocké"""
    from services.ml_service import MLService

    service = MLService()
    service.model = Mock()
    service.processor = Mock()
    service.device = "cpu"
    return service


# ── Comment lancer les tests ──────────────────────────────────────────────────
"""
Installation :
    pip install pytest pytest-asyncio

Lancer tous les tests :
    pytest

Lancer avec verbose :
    pytest -v

Lancer un fichier spécifique :
    pytest tests/test_services.py

Lancer un test spécifique :
    pytest tests/test_services.py::test_job_manager_create_job

Voir la couverture :
    pip install pytest-cov
    pytest --cov=services --cov-report=html
"""