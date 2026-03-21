"""
Tests unitaires pour utils/url_normalizer.py
"""
import pytest
from unittest.mock import AsyncMock, patch

from utils.url_normalizer import normalize_url, _normalize_from_long_url


class TestNormalizeFromLongUrl:
    def test_tiktok_long_url(self):
        url = "https://www.tiktok.com/@user/video/7123456789012345678"
        assert _normalize_from_long_url(url) == "tiktok.com/video/7123456789012345678"

    def test_tiktok_long_url_with_query_params(self):
        url = "https://www.tiktok.com/@user/video/7123456789012345678?lang=fr"
        assert _normalize_from_long_url(url) == "tiktok.com/video/7123456789012345678"

    def test_instagram_reel(self):
        url = "https://www.instagram.com/reel/CxYz1234abc/"
        assert _normalize_from_long_url(url) == "instagram.com/reel/CxYz1234abc"

    def test_instagram_reel_with_query_params(self):
        url = "https://www.instagram.com/reel/CxYz1234abc/?igsh=abc123"
        assert _normalize_from_long_url(url) == "instagram.com/reel/CxYz1234abc"

    def test_instagram_post(self):
        url = "https://www.instagram.com/p/CxYz1234abc/"
        assert _normalize_from_long_url(url) == "instagram.com/p/CxYz1234abc"

    def test_unknown_url_returns_none(self):
        assert _normalize_from_long_url("https://youtube.com/watch?v=abc") is None

    def test_none_like_empty_string_returns_none(self):
        assert _normalize_from_long_url("not a url at all") is None


class TestNormalizeUrl:
    @pytest.mark.anyio
    async def test_tiktok_long_url(self):
        url = "https://www.tiktok.com/@user/video/7123456789012345678"
        result = await normalize_url(url)
        assert result == "tiktok.com/video/7123456789012345678"

    @pytest.mark.anyio
    async def test_instagram_reel(self):
        url = "https://www.instagram.com/reel/CxYz1234abc/"
        result = await normalize_url(url)
        assert result == "instagram.com/reel/CxYz1234abc"

    @pytest.mark.anyio
    async def test_tiktok_short_url_resolved(self):
        resolved = "https://www.tiktok.com/@user/video/7999888777666555444"
        with patch(
            "utils.url_normalizer._resolve_short_url",
            new=AsyncMock(return_value=resolved),
        ):
            result = await normalize_url("https://vm.tiktok.com/ZMhAbCdEf/")
            assert result == "tiktok.com/video/7999888777666555444"

    @pytest.mark.anyio
    async def test_tiktok_vt_short_url_resolved(self):
        resolved = "https://www.tiktok.com/@user/video/1111222233334444555"
        with patch(
            "utils.url_normalizer._resolve_short_url",
            new=AsyncMock(return_value=resolved),
        ):
            result = await normalize_url("https://vt.tiktok.com/ZSomeCode/")
            assert result == "tiktok.com/video/1111222233334444555"

    @pytest.mark.anyio
    async def test_tiktok_short_url_resolution_fails_fallback(self):
        with patch(
            "utils.url_normalizer._resolve_short_url",
            new=AsyncMock(return_value=None),
        ):
            result = await normalize_url("https://vm.tiktok.com/ZMhAbCdEf/")
            # Fallback: cleaned URL
            assert result == "https://vm.tiktok.com/ZMhAbCdEf"

    @pytest.mark.anyio
    async def test_unknown_url_fallback_strips_query_and_fragment(self):
        url = "https://youtube.com/watch?v=abc#comments"
        result = await normalize_url(url)
        assert result == "https://youtube.com/watch"

    @pytest.mark.anyio
    async def test_whitespace_stripped(self):
        url = "  https://www.tiktok.com/@user/video/9999  "
        result = await normalize_url(url)
        assert result == "tiktok.com/video/9999"

    @pytest.mark.anyio
    async def test_same_video_different_tiktok_formats_normalize_identically(self):
        long_url = "https://www.tiktok.com/@someuser/video/7123456789012345678?lang=en"
        resolved = "https://www.tiktok.com/@someuser/video/7123456789012345678"

        with patch(
            "utils.url_normalizer._resolve_short_url",
            new=AsyncMock(return_value=resolved),
        ):
            result_long = await normalize_url(long_url)
            result_short = await normalize_url("https://vm.tiktok.com/ZMhAbCdEf/")

        assert result_long == result_short == "tiktok.com/video/7123456789012345678"
