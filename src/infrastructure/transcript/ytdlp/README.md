# transcript/ytdlp

## Overview
yt-dlp adapter implementing `TranscriptProviderPort`. Downloads subtitles for a YouTube video, falls back to another language when the preferred one is missing, and cleans SRT/VTT text into plain transcript text.

## Interface
- `YtDlpTranscriptProvider` (`provider.py`): async `fetch_transcript(...)` and `fetch_transcripts(...)` (`asyncio.gather`: every video is attempted, then the first error in input order is re-raised).
- `download_subtitles(options, urls)`, `list_subtitle_languages(options, url)`, `pick_fallback_language(available)`.
- `clean_transcript_text(raw)`: strips cue indexes, timings, inline tags, WEBVTT headers and NOTE/STYLE/REGION blocks.
- `redact_secrets(detail)`, `safe_detail(detail)`: sanitize provider error text.
- Constants: `WATCH_URL`, `PROVIDER_NAME = "ytdlp"`, `OPERATION_NAME = "fetch_transcript"`.
- Package `__init__` re-exports `YtDlpTranscriptProvider`, `clean_transcript_text`, `download_subtitles`.

## Invariants
- Only `.srt` and `.vtt` subtitle files are consumed.
- Error detail is redacted before being surfaced or logged.
- The adapter does not own retry policy; callers do.
- Fetches are serialised by an asyncio lock and spaced by `transcript_min_interval_seconds` (default 4.0, plus up to 25% jitter) via `_paced()`.
- `ytdlp_cookies_file` takes precedence over `ytdlp_cookies_from_browser`.

## Side Effects
- Network calls to YouTube via yt-dlp.
- Writes a zero-token `UsageRecord` (provider/model `ytdlp`, operation `fetch_transcript`) to the usage ledger on every attempt; ledger failures are logged, never raised.
- Downloads subtitles into a temporary work directory, then persists the raw subtitle file into `transcript_output_folder` as `<video_id>.<uuid12>.<ext>` and keeps it (`result.artifact_path`); may read a cookies file when configured.

## Verification
- Run: `pytest` (from repo root)
