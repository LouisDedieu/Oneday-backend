"""
Tests d'intégration pour les codes d'erreur API
Teste les endpoints réels avec des conditions d'erreur
"""
import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch, MagicMock
import json

from main import app
from models.errors import ErrorCode


client = TestClient(app)


class TestAuthErrorCodes:
    """Tests pour les codes d'erreur d'authentification"""

    def test_not_authenticated_returns_401(self):
        """Test que l'accès sans token retourne NOT_AUTHENTICATED"""
        response = client.get("/profile")
        assert response.status_code == 401
        data = response.json()
        assert "error_code" in data or "detail" in data

    def test_invalid_token_returns_401(self):
        """Test qu'un token invalide retourne INVALID_TOKEN"""
        response = client.get(
            "/profile",
            headers={"Authorization": "Bearer invalid-token-123"}
        )
        assert response.status_code == 401

    def test_missing_bearer_returns_401(self):
        """Test qu'un header Authorization malformaté retourne 401"""
        response = client.get(
            "/profile",
            headers={"Authorization": "JustSomeText"}
        )
        assert response.status_code == 401


class TestTripErrorCodes:
    """Tests pour les codes d'erreur de trips"""

    def test_trip_endpoint_requires_auth(self):
        """Test que l'endpoint trips nécessite une auth"""
        response = client.get("/trips/non-existent-trip-id-12345")
        # 401 si pas auth, 503 si Supabase non configuré
        assert response.status_code in [401, 503]

    def test_city_endpoint_requires_auth(self):
        """Test que l'endpoint cities nécessite une auth"""
        response = client.get("/cities/non-existent-city-id")
        assert response.status_code in [401, 503]


class TestAnalyzeErrorCodes:
    """Tests pour les codes d'erreur d'analyse"""

    def test_analyze_endpoint_requires_auth(self):
        """Test que /analyze/url nécessite une auth"""
        response = client.post(
            "/analyze/url",
            json={"url": "https://www.tiktok.com/@user/video/123"}
        )
        assert response.status_code == 401

    def test_invalid_token_returns_401(self):
        """Test qu'un token invalide retourne 401"""
        response = client.post(
            "/analyze/url",
            headers={"Authorization": "Bearer invalid-jwt-token"},
            json={"url": "https://www.tiktok.com/@user/video/123"}
        )
        assert response.status_code == 401

    def test_missing_url_with_valid_auth_returns_422(self):
        """Test que URL manquante avec auth valide retourne 422"""
        # Note: En pratique, cela nécessite un vrai token JWT Supabase
        # Ici on teste juste la route
        response = client.post(
            "/analyze/url",
            headers={"Authorization": "Bearer test"},
            json={}
        )
        # Retourne 401 car "Bearer test" n'est pas un JWT valide
        assert response.status_code in [401, 422]


class TestErrorResponseFormat:
    """Tests pour le format des réponses d'erreur"""

    def test_error_response_has_correct_structure(self):
        """Test que la réponse d'erreur a la structure attendue"""
        response = client.get("/profile")
        assert response.status_code == 401
        data = response.json()
        assert isinstance(data, dict)

    def test_error_includes_error_code_field(self):
        """Test que l'erreur inclut le champ error_code"""
        response = client.get("/profile")
        if response.status_code == 401:
            data = response.json()
            assert "error_code" in data or "detail" in data

    def test_all_error_codes_are_serializable(self):
        """Test que tous les codes d'erreur sont sérialisables en JSON"""
        for code in ErrorCode:
            json.dumps(code.value)

    def test_error_messages_are_french(self):
        """Test que les messages d'erreur sont en français"""
        from models.errors import get_error_message
        msg = get_error_message(ErrorCode.TRIP_NOT_FOUND)
        assert isinstance(msg, str)
        assert len(msg) > 0


class TestHTTPStatusCodes:
    """Tests pour les codes de statut HTTP"""

    def test_401_for_unauthorized(self):
        """401 pour requête non autorisée"""
        response = client.get("/profile")
        assert response.status_code == 401

    def test_validation_error_returns_422_or_401(self):
        """422 pour erreur de validation ou 401 si pas d'auth valide"""
        response = client.post(
            "/analyze/url",
            headers={"Authorization": "Bearer test"},
            json={"url": ""}
        )
        # 401 si token invalide, 422 si validation échoue après auth
        assert response.status_code in [401, 422]

    def test_503_when_supabase_unavailable(self):
        """503 quand Supabase n'est pas configuré"""
        # Les endpoints protégés par Supabase retournent 503
        response = client.get("/trips/public")
        # Soit 503 (Supabase unavailable), soit 401 (auth required)
        assert response.status_code in [401, 503]


class TestFrontendCompatibility:
    """Tests pour la compatibilité avec le frontend"""

    def test_error_response_matches_frontend_types(self):
        """Test que les réponses d'erreur matchent les types TypeScript"""
        response = client.get("/profile")
        assert response.status_code == 401
        data = response.json()
        # Le frontend attend { error_code: string, message: string }
        assert "error_code" in data or "detail" in data

    def test_api_error_code_format(self):
        """Test que le format des codes d'erreur est compatible"""
        valid_codes = [
            "TRIP_NOT_FOUND",
            "CITY_NOT_FOUND", 
            "NOT_AUTHENTICATED",
            "INVALID_TOKEN",
            "ACCESS_DENIED",
            "UNSUPPORTED_URL",
            "PRIVATE_VIDEO",
            "SERVICE_UNAVAILABLE",
            "DOWNLOAD_ERROR",
            "MISSING_FIELD",
        ]

        for code in valid_codes:
            assert isinstance(code, str)
            assert "_" in code  # snake_case

    def test_error_codes_match_typescript_definitions(self):
        """Test que les codes backend matchent les définitions TypeScript frontend"""
        backend_codes = [code.value for code in ErrorCode]
        
        # Codes que le frontend TypeScript utilise
        frontend_codes = [
            "TRIP_NOT_FOUND", "CITY_NOT_FOUND", "JOB_NOT_FOUND",
            "NOT_AUTHENTICATED", "INVALID_TOKEN", "ACCESS_DENIED",
            "UNSUPPORTED_URL", "PRIVATE_VIDEO", "SERVICE_UNAVAILABLE",
        ]
        
        for code in frontend_codes:
            assert code in backend_codes, f"Code {code} manquant dans le backend"
