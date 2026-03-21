"""
Tests du flow de déduplication vidéo (clonage au lieu de ré-analyse).
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch, call

from services.job_processor import JobProcessor


def _make_request(url: str, user_id: str = "user-new"):
    req = MagicMock()
    req.url = url
    req.user_id = user_id
    req.cookies_file = None
    req.proxy = None
    req.entity_type_override = None
    return req


@pytest.fixture
def supabase():
    svc = AsyncMock()
    svc.is_configured.return_value = True
    return svc


@pytest.fixture
def processor(supabase):
    return JobProcessor(supabase_service=supabase)


# ── find_trip_by_source_url ────────────────────────────────────────────────

class TestFindBySourceUrl:
    @pytest.mark.anyio
    async def test_returns_none_when_not_configured(self):
        from services.supabase_service import SupabaseService
        svc = SupabaseService()  # no url/key
        result = await svc.find_trip_by_source_url("tiktok.com/video/123")
        assert result is None

    @pytest.mark.anyio
    async def test_returns_none_when_not_configured_city(self):
        from services.supabase_service import SupabaseService
        svc = SupabaseService()
        result = await svc.find_city_by_source_url("tiktok.com/video/123")
        assert result is None


# ── Dedup flow in job_processor ───────────────────────────────────────────

class TestDedupFlow:
    @pytest.mark.anyio
    async def test_trip_clone_skips_download_and_analysis(self, processor, supabase):
        """Si un trip avec la même URL existe, on clone sans télécharger ni analyser."""
        existing_trip = {"id": "trip-original", "trip_title": "Tokyo Trip", "type": "trip"}
        supabase.find_trip_by_source_url.return_value = existing_trip
        supabase.find_city_by_source_url.return_value = None
        supabase.clone_trip_for_user.return_value = "trip-clone-123"
        supabase.update_job.return_value = None

        request = _make_request("https://www.tiktok.com/@user/video/7123456789")

        with patch("utils.url_normalizer.normalize_url", new=AsyncMock(return_value="tiktok.com/video/7123456789")), \
             patch("services.job_processor.job_manager") as mock_jm, \
             patch("services.job_processor.download_video") as mock_dl:

            mock_jm.send_sse_update = AsyncMock()
            mock_jm.update_job_status = MagicMock()

            await processor.process_url_job("job-1", request)

        # Clone was called with the right args
        supabase.clone_trip_for_user.assert_awaited_once_with(
            "trip-original", "job-1", "user-new"
        )
        # Download was never called
        mock_dl.assert_not_called()

        # SSE done event was sent
        sent_calls = mock_jm.send_sse_update.await_args_list
        statuses = [c.args[1] for c in sent_calls]
        assert "done" in statuses

        # Response has cloned=True
        done_call = next(c for c in sent_calls if c.args[1] == "done")
        result = done_call.args[2]["result"]
        assert result["cloned"] is True
        assert result["trip_id"] == "trip-clone-123"
        assert result["cloned_from"] == "trip-original"

    @pytest.mark.anyio
    async def test_city_clone_skips_download_and_analysis(self, processor, supabase):
        """Si une city avec la même URL existe, on clone sans télécharger ni analyser."""
        supabase.find_trip_by_source_url.return_value = None
        existing_city = {"id": "city-original", "city_title": "Paris Guide", "type": "city"}
        supabase.find_city_by_source_url.return_value = existing_city
        supabase.clone_city_for_user.return_value = "city-clone-456"
        supabase.update_job.return_value = None

        request = _make_request("https://www.instagram.com/reel/CxYz1234abc/")

        with patch("utils.url_normalizer.normalize_url", new=AsyncMock(return_value="instagram.com/reel/CxYz1234abc")), \
             patch("services.job_processor.job_manager") as mock_jm, \
             patch("services.job_processor.download_video") as mock_dl:

            mock_jm.send_sse_update = AsyncMock()
            mock_jm.update_job_status = MagicMock()

            await processor.process_url_job("job-2", request)

        supabase.clone_city_for_user.assert_awaited_once_with(
            "city-original", "job-2", "user-new"
        )
        mock_dl.assert_not_called()

        sent_calls = mock_jm.send_sse_update.await_args_list
        done_call = next(c for c in sent_calls if c.args[1] == "done")
        result = done_call.args[2]["result"]
        assert result["cloned"] is True
        assert result["city_id"] == "city-clone-456"

    @pytest.mark.anyio
    async def test_clone_failure_falls_through_to_normal_flow(self, processor, supabase):
        """Si le clonage échoue, on retombe sur le flow classique (téléchargement)."""
        existing_trip = {"id": "trip-original", "trip_title": "Tokyo Trip", "type": "trip"}
        supabase.find_trip_by_source_url.return_value = existing_trip
        supabase.find_city_by_source_url.return_value = None
        supabase.clone_trip_for_user.return_value = None  # clone fails

        request = _make_request("https://www.tiktok.com/@user/video/7123456789")

        with patch("utils.url_normalizer.normalize_url", new=AsyncMock(return_value="tiktok.com/video/7123456789")), \
             patch("services.job_processor.job_manager") as mock_jm, \
             patch("services.job_processor.download_video", new=AsyncMock(side_effect=Exception("download kicked off"))) as mock_dl:

            mock_jm.send_sse_update = AsyncMock()
            mock_jm.update_job_status = MagicMock()

            await processor.process_url_job("job-3", request)

        # Download was called (normal flow resumed)
        mock_dl.assert_called_once()

    @pytest.mark.anyio
    async def test_no_duplicate_triggers_normal_flow(self, processor, supabase):
        """Sans doublon, le flow classique (téléchargement) démarre."""
        supabase.find_trip_by_source_url.return_value = None
        supabase.find_city_by_source_url.return_value = None

        request = _make_request("https://www.tiktok.com/@user/video/9999999")

        with patch("utils.url_normalizer.normalize_url", new=AsyncMock(return_value="tiktok.com/video/9999999")), \
             patch("services.job_processor.job_manager") as mock_jm, \
             patch("services.job_processor.download_video", new=AsyncMock(side_effect=Exception("download kicked off"))) as mock_dl:

            mock_jm.send_sse_update = AsyncMock()
            mock_jm.update_job_status = MagicMock()

            await processor.process_url_job("job-4", request)

        mock_dl.assert_called_once()
        supabase.clone_trip_for_user.assert_not_called()
        supabase.clone_city_for_user.assert_not_called()
