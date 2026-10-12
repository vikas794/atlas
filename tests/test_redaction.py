"""src.shared.redaction: Google/Gemini keys, OAuth tokens and named secrets never survive."""

from __future__ import annotations

import pytest

from src.shared.redaction import REDACTED, redact_text

GOOGLE_KEY = "AIzaSyA1234567890abcdefghijklmnopqrstu"
ACCESS_TOKEN = "ya29.a0AfH6SMBxyzxyzxyzxyzxyz"
REFRESH_TOKEN = "1//0gabcdefghijklmnop"
CLIENT_SECRET = "GOCSPX-abcdefghijklmnopqrstuvwx"
OPENROUTER_KEY = "sk-or-v1-0123456789abcdef0123456789abcdef"


@pytest.mark.parametrize(
    ("text", "secret"),
    [
        (f"GET https://www.googleapis.com/youtube/v3/playlists?part=snippet&key={GOOGLE_KEY}&alt=json", GOOGLE_KEY),
        (f"API key not valid. Please pass a valid API key: {GOOGLE_KEY}", GOOGLE_KEY),
        (f"x-goog-api-key: {GOOGLE_KEY}", GOOGLE_KEY),
        (f'{{"access_token": "{ACCESS_TOKEN}", "expires_in": 3599}}', ACCESS_TOKEN),
        (f'{{"refresh_token": "{REFRESH_TOKEN}"}}', REFRESH_TOKEN),
        (f"refresh_token={REFRESH_TOKEN}", REFRESH_TOKEN),
        (f"{{'refresh_token': '{REFRESH_TOKEN}'}}", REFRESH_TOKEN),
        (f'{{"client_secret": "{CLIENT_SECRET}"}}', CLIENT_SECRET),
        (f"token expired: {ACCESS_TOKEN}", ACCESS_TOKEN),
        (f"Authorization: Bearer {ACCESS_TOKEN}", ACCESS_TOKEN),
        (f"openrouter rejected {OPENROUTER_KEY}", OPENROUTER_KEY),
        ("password=hunter2hunter2", "hunter2hunter2"),
        ('{"api_key":"plainvalue123"}', "plainvalue123"),
    ],
)
def test_secret_never_survives(text: str, secret: str) -> None:
    out = redact_text(text)
    assert secret not in out
    assert REDACTED in out


def test_explicit_secrets_are_replaced_verbatim() -> None:
    assert redact_text("value is abcdefgh12345 here", "abcdefgh12345") == f"value is {REDACTED} here"


def test_short_explicit_secrets_are_ignored() -> None:
    assert redact_text("abc", "abc") == "abc"


def test_ordinary_text_is_untouched() -> None:
    text = "Fetched 42 videos for playlist PLabc (monkey keyboard tokenizer) video_id=dQw4w9WgXcQ"
    assert redact_text(text) == text


def test_non_strings_are_stringified() -> None:
    assert GOOGLE_KEY not in redact_text(ValueError(f"bad key {GOOGLE_KEY}"))
