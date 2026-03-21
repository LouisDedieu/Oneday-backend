"""
Tests pour les codes d'erreur API
"""
import pytest
from fastapi.testclient import TestClient
from models.errors import ErrorCode, ErrorResponse, get_error_message, ERROR_MESSAGES
from models.schemas import AnalyzeUrlRequest


class TestErrorCodes:
    """Tests pour les codes d'erreur"""

    def test_all_error_codes_are_strings(self):
        """Vérifie que tous les codes sont des strings"""
        for code in ErrorCode:
            assert isinstance(code.value, str)

    def test_error_codes_are_unique(self):
        """Vérifie que tous les codes sont uniques"""
        values = [code.value for code in ErrorCode]
        assert len(values) == len(set(values))

    def test_analyse_error_codes(self):
        """Test les codes d'erreur d'analyse"""
        assert ErrorCode.UNSUPPORTED_URL.value == "UNSUPPORTED_URL"
        assert ErrorCode.PRIVATE_VIDEO.value == "PRIVATE_VIDEO"
        assert ErrorCode.IP_BLOCKED.value == "IP_BLOCKED"
        assert ErrorCode.DOWNLOAD_ERROR.value == "DOWNLOAD_ERROR"
        assert ErrorCode.INFERENCE_ERROR.value == "INFERENCE_ERROR"
        assert ErrorCode.MODEL_NOT_LOADED.value == "MODEL_NOT_LOADED"
        assert ErrorCode.SERVICE_UNAVAILABLE.value == "SERVICE_UNAVAILABLE"

    def test_resource_error_codes(self):
        """Test les codes d'erreur de ressource"""
        assert ErrorCode.TRIP_NOT_FOUND.value == "TRIP_NOT_FOUND"
        assert ErrorCode.CITY_NOT_FOUND.value == "CITY_NOT_FOUND"
        assert ErrorCode.JOB_NOT_FOUND.value == "JOB_NOT_FOUND"
        assert ErrorCode.HIGHLIGHT_NOT_FOUND.value == "HIGHLIGHT_NOT_FOUND"
        assert ErrorCode.DESTINATION_NOT_FOUND.value == "DESTINATION_NOT_FOUND"

    def test_auth_error_codes(self):
        """Test les codes d'erreur d'authentification"""
        assert ErrorCode.ACCESS_DENIED.value == "ACCESS_DENIED"
        assert ErrorCode.NOT_AUTHENTICATED.value == "NOT_AUTHENTICATED"
        assert ErrorCode.INVALID_TOKEN.value == "INVALID_TOKEN"

    def test_validation_error_codes(self):
        """Test les codes d'erreur de validation"""
        assert ErrorCode.INVALID_REQUEST.value == "INVALID_REQUEST"
        assert ErrorCode.MISSING_FIELD.value == "MISSING_FIELD"


class TestErrorMessages:
    """Tests pour les messages d'erreur"""

    def test_all_codes_have_messages(self):
        """Vérifie que tous les codes ont un message"""
        for code in ErrorCode:
            assert code in ERROR_MESSAGES
            assert ERROR_MESSAGES[code]

    def test_get_error_message_returns_string(self):
        """Vérifie que get_error_message retourne une string"""
        for code in ErrorCode:
            msg = get_error_message(code)
            assert isinstance(msg, str)
            assert len(msg) > 0

    def test_get_error_message_unknown(self):
        """Test le fallback pour code inconnu"""
        # Créer un mock ErrorCode
        class FakeCode:
            value = "FAKE_CODE"
        msg = get_error_message(FakeCode())
        assert msg == ERROR_MESSAGES[ErrorCode.UNKNOWN_ERROR]

    def test_french_messages(self):
        """Vérifie que les messages sont en français"""
        msg = get_error_message(ErrorCode.TRIP_NOT_FOUND)
        assert "introuvable" in msg.lower()


class TestErrorResponse:
    """Tests pour le modèle ErrorResponse"""

    def test_error_response_required_fields(self):
        """Test les champs requis"""
        response = ErrorResponse(
            error_code=ErrorCode.TRIP_NOT_FOUND,
            message="Voyage introuvable"
        )
        assert response.error_code == ErrorCode.TRIP_NOT_FOUND
        assert response.message == "Voyage introuvable"

    def test_error_response_with_details(self):
        """Test avec détails optionnels"""
        response = ErrorResponse(
            error_code=ErrorCode.MISSING_FIELD,
            message="Champ manquant",
            details=[]
        )
        assert response.details == []

    def test_error_response_serialization(self):
        """Test la sérialisation JSON"""
        response = ErrorResponse(
            error_code=ErrorCode.INVALID_TOKEN,
            message="Token invalide"
        )
        json_data = response.model_dump()
        assert json_data["error_code"] == "INVALID_TOKEN"
        assert json_data["message"] == "Token invalide"

    def test_error_response_with_field(self):
        """Test ErrorDetail avec champ"""
        response = ErrorResponse(
            error_code=ErrorCode.MISSING_FIELD,
            message="Champ requis",
            details=[{
                "code": ErrorCode.MISSING_FIELD,
                "message": "Le champ 'url' est requis",
                "field": "url"
            }]
        )
        assert len(response.details) == 1
        assert response.details[0].field == "url"


class TestAnalyzeUrlValidation:
    """Tests pour la validation des URLs d'analyse"""

    def test_valid_tiktok_url(self):
        """Test URL TikTok valide"""
        request = AnalyzeUrlRequest(url="https://www.tiktok.com/@user/video/123")
        assert "tiktok" in request.url

    def test_valid_instagram_url(self):
        """Test URL Instagram valide"""
        request = AnalyzeUrlRequest(url="https://www.instagram.com/reel/ABC123/")
        assert "instagram" in request.url

    def test_valid_youtube_url(self):
        """Test URL YouTube valide"""
        request = AnalyzeUrlRequest(url="https://www.youtube.com/watch?v=dQw4w9WgXcQ")
        assert "youtube" in request.url

    def test_accepts_any_string_url(self):
        """Test que AnalyzeUrlRequest accepte n'importe quelle string (pas de validation HttpUrl)"""
        request = AnalyzeUrlRequest(url="not-a-valid-url")
        assert request.url == "not-a-valid-url"

    def test_empty_url_allowed(self):
        """Test que URL vide est acceptée (pas de validation)"""
        request = AnalyzeUrlRequest(url="")
        assert request.url == ""

    def test_url_with_user_id(self):
        """Test URL avec user_id optionnel"""
        request = AnalyzeUrlRequest(url="https://tiktok.com/video/123", user_id="user-456")
        assert request.user_id == "user-456"


@pytest.fixture
def client():
    """Fixture pour le client de test FastAPI"""
    from main import app
    return TestClient(app)


class TestAPIErrorEndpoints:
    """Tests d'intégration pour les endpoints d'erreur"""

    def test_trip_not_found_returns_404(self, client):
        """Test que trip non trouvé retourne 404"""
        response = client.get("/api/trips/non-existant-id-12345")
        assert response.status_code == 404
        data = response.json()
        assert "error_code" in data or "detail" in data

    def test_city_not_found_returns_404(self, client):
        """Test que ville non trouvée retourne 404"""
        response = client.get("/api/cities/non-existant")
        assert response.status_code == 404

    def test_profile_without_auth_returns_401_or_403(self, client):
        """Test que l'accès au profil sans auth retourne 401 ou 403"""
        response = client.get("/profile")
        assert response.status_code in [401, 403]

    def test_profile_with_invalid_token_returns_401_or_403(self, client):
        """Test qu'un token invalide retourne 401 ou 403"""
        response = client.get(
            "/profile",
            headers={"Authorization": "Bearer invalid-token"}
        )
        assert response.status_code in [401, 403]


class TestErrorScenarios:
    """Tests pour différents scénarios d'erreur"""

    def test_unsupported_url_error(self):
        """Test le code UNSUPPORTED_URL"""
        error = ErrorResponse(
            error_code=ErrorCode.UNSUPPORTED_URL,
            message=get_error_message(ErrorCode.UNSUPPORTED_URL)
        )
        assert "URL" in error.message or "url" in error.message.lower()

    def test_private_video_error(self):
        """Test le code PRIVATE_VIDEO"""
        error = ErrorResponse(
            error_code=ErrorCode.PRIVATE_VIDEO,
            message=get_error_message(ErrorCode.PRIVATE_VIDEO)
        )
        assert "privée" in error.message.lower() or "disponible" in error.message.lower()

    def test_download_error(self):
        """Test le code DOWNLOAD_ERROR"""
        error = ErrorResponse(
            error_code=ErrorCode.DOWNLOAD_ERROR,
            message=get_error_message(ErrorCode.DOWNLOAD_ERROR)
        )
        assert "télécharger" in error.message.lower() or "download" in error.message.lower()

    def test_model_not_loaded_error(self):
        """Test le code MODEL_NOT_LOADED"""
        error = ErrorResponse(
            error_code=ErrorCode.MODEL_NOT_LOADED,
            message=get_error_message(ErrorCode.MODEL_NOT_LOADED)
        )
        assert "modèle" in error.message.lower() or "model" in error.message.lower()

    def test_service_unavailable_error(self):
        """Test le code SERVICE_UNAVAILABLE"""
        error = ErrorResponse(
            error_code=ErrorCode.SERVICE_UNAVAILABLE,
            message=get_error_message(ErrorCode.SERVICE_UNAVAILABLE)
        )
        assert "disponible" in error.message.lower() or "available" in error.message.lower()

    def test_access_denied_error(self):
        """Test le code ACCESS_DENIED"""
        error = ErrorResponse(
            error_code=ErrorCode.ACCESS_DENIED,
            message=get_error_message(ErrorCode.ACCESS_DENIED)
        )
        assert "accès" in error.message.lower() or "denied" in error.message.lower()

    def test_invalid_request_error(self):
        """Test le code INVALID_REQUEST"""
        error = ErrorResponse(
            error_code=ErrorCode.INVALID_REQUEST,
            message=get_error_message(ErrorCode.INVALID_REQUEST)
        )
        assert "requête" in error.message.lower() or "request" in error.message.lower()
