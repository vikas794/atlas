from __future__ import annotations

import pytest

from src.domain.models.playlist import FailureType, PipelineError
from src.domain.services.playlist_url import parse_playlist_url


def test_parse_standard_playlist_url() -> None:
    url = "https://www.youtube.com/playlist?list=PL1234567890abcdef"
    assert parse_playlist_url(url) == "PL1234567890abcdef"


def test_parse_watch_url_with_list() -> None:
    url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ&list=PLabc123&index=3"
    assert parse_playlist_url(url) == "PLabc123"


def test_parse_short_url_with_list() -> None:
    url = "https://youtu.be/dQw4w9WgXcQ?list=PLshort123&si=tracking"
    assert parse_playlist_url(url) == "PLshort123"


def test_parse_url_without_scheme() -> None:
    url = "youtube.com/playlist?list=PLnoscheme"
    assert parse_playlist_url(url) == "PLnoscheme"


def test_parse_url_with_extra_params() -> None:
    url = "https://music.youtube.com/playlist?list=PLmusic&si=foo&feature=share"
    assert parse_playlist_url(url) == "PLmusic"


def test_invalid_domain_raises_invalid_url() -> None:
    with pytest.raises(PipelineError) as exc_info:
        parse_playlist_url("https://vimeo.com/playlist?list=PL123")
    assert exc_info.value.failure_type == FailureType.INVALID_URL


def test_missing_list_param_raises_invalid_url() -> None:
    with pytest.raises(PipelineError) as exc_info:
        parse_playlist_url("https://www.youtube.com/watch?v=dQw4w9WgXcQ")
    assert exc_info.value.failure_type == FailureType.INVALID_URL


def test_empty_url_raises_invalid_url() -> None:
    with pytest.raises(PipelineError) as exc_info:
        parse_playlist_url("")
    assert exc_info.value.failure_type == FailureType.INVALID_URL
