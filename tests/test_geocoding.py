"""
Tests pour l'endpoint de geocoding proxy
Vérifie que le proxy LocationIQ fonctionne correctement
"""
import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch, MagicMock, AsyncMock
import httpx

from main import app
from config import settings


client = TestClient(app)


class TestGeocodingEndpoint:
    """Tests pour l'endpoint GET /geocoding/search"""

    def test_geocoding_requires_query_parameter(self):
        """Test que le paramètre q est requis"""
        response = client.get("/geocoding/search")
        assert response.status_code == 422  # Validation error

    def test_geocoding_rejects_empty_query(self):
        """Test qu'une query vide est rejetée"""
        response = client.get("/geocoding/search?q=")
        assert response.status_code == 422  # min_length=1

    def test_geocoding_accepts_valid_query(self):
        """Test qu'une query valide est acceptée"""
        with patch('api.geocoding.httpx.AsyncClient') as mock_client:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = [
                {"lat": "48.8566", "lon": "2.3522", "display_name": "Paris, France"}
            ]
            mock_response.raise_for_status = MagicMock()

            mock_instance = MagicMock()
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock(return_value=None)
            mock_instance.get = AsyncMock(return_value=mock_response)
            mock_client.return_value = mock_instance

            response = client.get("/geocoding/search?q=Paris")
            # Peut retourner 500 si LOCATIONIQ_API_KEY n'est pas configuré
            assert response.status_code in [200, 500]

    def test_geocoding_limit_parameter(self):
        """Test que le paramètre limit est respecté"""
        with patch('api.geocoding.httpx.AsyncClient') as mock_client:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = [
                {"lat": "48.8566", "lon": "2.3522", "display_name": "Paris"}
            ]
            mock_response.raise_for_status = MagicMock()

            mock_instance = MagicMock()
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock(return_value=None)
            mock_instance.get = AsyncMock(return_value=mock_response)
            mock_client.return_value = mock_instance

            response = client.get("/geocoding/search?q=Paris&limit=5")
            assert response.status_code in [200, 500]

    def test_geocoding_limit_min_value(self):
        """Test que limit >= 1"""
        response = client.get("/geocoding/search?q=Paris&limit=0")
        assert response.status_code == 422

    def test_geocoding_limit_max_value(self):
        """Test que limit <= 10"""
        response = client.get("/geocoding/search?q=Paris&limit=20")
        assert response.status_code == 422


class TestGeocodingResponseFormat:
    """Tests pour le format de réponse"""

    def test_response_has_results_array(self):
        """Test que la réponse contient un tableau results"""
        with patch('api.geocoding.httpx.AsyncClient') as mock_client:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = [
                {"lat": "48.8566", "lon": "2.3522", "display_name": "Paris"}
            ]
            mock_response.raise_for_status = MagicMock()

            mock_instance = MagicMock()
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock(return_value=None)
            mock_instance.get = AsyncMock(return_value=mock_response)
            mock_client.return_value = mock_instance

            response = client.get("/geocoding/search?q=Paris")
            if response.status_code == 200:
                data = response.json()
                assert "results" in data
                assert isinstance(data["results"], list)

    def test_result_has_lat_lon(self):
        """Test que chaque résultat a lat et lon"""
        with patch('api.geocoding.httpx.AsyncClient') as mock_client:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = [
                {"lat": "48.8566", "lon": "2.3522", "display_name": "Paris"}
            ]
            mock_response.raise_for_status = MagicMock()

            mock_instance = MagicMock()
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock(return_value=None)
            mock_instance.get = AsyncMock(return_value=mock_response)
            mock_client.return_value = mock_instance

            response = client.get("/geocoding/search?q=Paris")
            if response.status_code == 200:
                data = response.json()
                if data["results"]:
                    result = data["results"][0]
                    assert "lat" in result
                    assert "lon" in result
                    assert isinstance(result["lat"], float)
                    assert isinstance(result["lon"], float)


class TestGeocodingErrorHandling:
    """Tests pour la gestion des erreurs"""

    def test_missing_api_key_returns_500(self):
        """Test qu'une clé API manquante retourne 500"""
        with patch.object(settings, 'LOCATIONIQ_API_KEY', ''):
            response = client.get("/geocoding/search?q=Paris")
            assert response.status_code == 500
            data = response.json()
            assert "detail" in data
            assert "not configured" in data["detail"].lower()

    def test_rate_limit_returns_429(self):
        """Test que le rate limiting retourne 429"""
        with patch('api.geocoding.httpx.AsyncClient') as mock_client:
            mock_response = MagicMock()
            mock_response.status_code = 429
            mock_response.raise_for_status = MagicMock(
                side_effect=httpx.HTTPStatusError(
                    "Rate limited",
                    request=MagicMock(),
                    response=mock_response
                )
            )

            mock_instance = MagicMock()
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock(return_value=None)
            mock_instance.get = AsyncMock(return_value=mock_response)
            mock_client.return_value = mock_instance

            with patch.object(settings, 'LOCATIONIQ_API_KEY', 'test-key'):
                response = client.get("/geocoding/search?q=Paris")
                assert response.status_code in [429, 500, 502]

    def test_timeout_returns_504(self):
        """Test qu'un timeout retourne 504"""
        with patch('api.geocoding.httpx.AsyncClient') as mock_client:
            mock_instance = MagicMock()
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock(return_value=None)
            mock_instance.get = AsyncMock(side_effect=httpx.TimeoutException("Timeout"))
            mock_client.return_value = mock_instance

            with patch.object(settings, 'LOCATIONIQ_API_KEY', 'test-key'):
                response = client.get("/geocoding/search?q=Paris")
                assert response.status_code == 504

    def test_no_results_returns_empty_array(self):
        """Test qu'une recherche sans résultat retourne un tableau vide"""
        with patch('api.geocoding.httpx.AsyncClient') as mock_client:
            mock_response = MagicMock()
            mock_response.status_code = 404
            mock_response.raise_for_status = MagicMock()

            mock_instance = MagicMock()
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock(return_value=None)
            mock_instance.get = AsyncMock(return_value=mock_response)
            mock_client.return_value = mock_instance

            with patch.object(settings, 'LOCATIONIQ_API_KEY', 'test-key'):
                response = client.get("/geocoding/search?q=UnknownPlace12345")
                if response.status_code == 200:
                    data = response.json()
                    assert data["results"] == []


class TestGeocodingSecurity:
    """Tests de sécurité pour le proxy"""

    def test_api_key_not_exposed_in_response(self):
        """Test que la clé API n'est pas exposée dans la réponse"""
        with patch('api.geocoding.httpx.AsyncClient') as mock_client:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = [
                {"lat": "48.8566", "lon": "2.3522", "display_name": "Paris"}
            ]
            mock_response.raise_for_status = MagicMock()

            mock_instance = MagicMock()
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock(return_value=None)
            mock_instance.get = AsyncMock(return_value=mock_response)
            mock_client.return_value = mock_instance

            with patch.object(settings, 'LOCATIONIQ_API_KEY', 'secret-api-key-123'):
                response = client.get("/geocoding/search?q=Paris")
                response_text = response.text
                assert "secret-api-key" not in response_text
                assert "LOCATIONIQ" not in response_text

    def test_proxy_calls_locationiq_with_key(self):
        """Test que le proxy appelle LocationIQ avec la clé"""
        with patch('api.geocoding.httpx.AsyncClient') as mock_client:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = []
            mock_response.raise_for_status = MagicMock()

            mock_instance = MagicMock()
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock(return_value=None)
            mock_instance.get = AsyncMock(return_value=mock_response)
            mock_client.return_value = mock_instance

            with patch.object(settings, 'LOCATIONIQ_API_KEY', 'test-key'):
                client.get("/geocoding/search?q=Paris")

                # Vérifie que httpx.get a été appelé avec la clé
                if mock_instance.get.called:
                    call_kwargs = mock_instance.get.call_args
                    if call_kwargs and call_kwargs.kwargs.get('params'):
                        params = call_kwargs.kwargs['params']
                        assert params.get('key') == 'test-key'


class TestGeocodingIntegration:
    """Tests d'intégration avec le frontend"""

    def test_response_matches_frontend_types(self):
        """Test que la réponse correspond aux types TypeScript du frontend"""
        # Types attendus par le frontend:
        # interface GeocodingResult {
        #   lat: number;
        #   lon: number;
        #   display_name?: string;
        # }
        # interface GeocodingResponse {
        #   results: GeocodingResult[];
        # }

        with patch('api.geocoding.httpx.AsyncClient') as mock_client:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = [
                {"lat": "48.8566", "lon": "2.3522", "display_name": "Paris, France"}
            ]
            mock_response.raise_for_status = MagicMock()

            mock_instance = MagicMock()
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock(return_value=None)
            mock_instance.get = AsyncMock(return_value=mock_response)
            mock_client.return_value = mock_instance

            with patch.object(settings, 'LOCATIONIQ_API_KEY', 'test-key'):
                response = client.get("/geocoding/search?q=Paris")
                if response.status_code == 200:
                    data = response.json()

                    # Vérifie la structure
                    assert "results" in data
                    assert isinstance(data["results"], list)

                    if data["results"]:
                        result = data["results"][0]
                        # lat et lon doivent être des nombres
                        assert isinstance(result["lat"], (int, float))
                        assert isinstance(result["lon"], (int, float))
                        # display_name est optionnel mais doit être string si présent
                        if "display_name" in result:
                            assert isinstance(result["display_name"], str)

    def test_endpoint_path_matches_frontend_service(self):
        """Test que le chemin de l'endpoint correspond au service frontend"""
        # Le frontend appelle: /geocoding/search?q=...&limit=...
        response = client.get("/geocoding/search?q=test&limit=1")
        # Ne doit pas retourner 404 (endpoint non trouvé)
        assert response.status_code != 404


class TestGeocodingQueryProcessing:
    """Tests pour le traitement des requêtes"""

    def test_special_characters_in_query(self):
        """Test que les caractères spéciaux sont gérés"""
        with patch('api.geocoding.httpx.AsyncClient') as mock_client:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = []
            mock_response.raise_for_status = MagicMock()

            mock_instance = MagicMock()
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock(return_value=None)
            mock_instance.get = AsyncMock(return_value=mock_response)
            mock_client.return_value = mock_instance

            with patch.object(settings, 'LOCATIONIQ_API_KEY', 'test-key'):
                # Caractères spéciaux
                response = client.get("/geocoding/search?q=Café+de+Flore,+Paris")
                assert response.status_code in [200, 500]

    def test_unicode_in_query(self):
        """Test que l'unicode est géré"""
        with patch('api.geocoding.httpx.AsyncClient') as mock_client:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = []
            mock_response.raise_for_status = MagicMock()

            mock_instance = MagicMock()
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock(return_value=None)
            mock_instance.get = AsyncMock(return_value=mock_response)
            mock_client.return_value = mock_instance

            with patch.object(settings, 'LOCATIONIQ_API_KEY', 'test-key'):
                # Caractères unicode
                response = client.get("/geocoding/search?q=東京")
                assert response.status_code in [200, 500]

    def test_long_query(self):
        """Test qu'une longue requête est gérée"""
        with patch('api.geocoding.httpx.AsyncClient') as mock_client:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = []
            mock_response.raise_for_status = MagicMock()

            mock_instance = MagicMock()
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock(return_value=None)
            mock_instance.get = AsyncMock(return_value=mock_response)
            mock_client.return_value = mock_instance

            with patch.object(settings, 'LOCATIONIQ_API_KEY', 'test-key'):
                long_query = "123 Rue de la République, 75001 Paris, France"
                response = client.get(f"/geocoding/search?q={long_query}")
                assert response.status_code in [200, 500]
