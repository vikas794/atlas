# domain/services

## Overview
Pure, stateless domain logic: pipeline state machine, failure classification, retry backoff, playlist diffing, Drive document naming, and Markdown-to-Google-Docs conversion.

## Interface
- `backoff.py`: `compute_delay`, `sleep_for` (async), `iso_after`.
- `doc_naming.py`: `sanitize_title`, `sanitize_drive_filename`, `pad_width`, `position_token`, `doc_name`, `doc_heading`, `render_doc_markdown`, `index_doc_name`, `playlist_folder_name`; constants `NAME_SEPARATOR`, `INDEX_DOC_SLOT`, `INDEX_DOC_MARKER`, `FALLBACK_TITLE`, `DEFAULT_MAX_TITLE_LENGTH`.
- `failure_classifier.py`: `FailureInfo`, `classify_error`, `classify_transcript_error`, `classify_output_error`, `classify_playlist_error`, `human_reason`, `safe_diagnostic`.
- `markdown_to_docs.py`: `InlineSpan`, `Block`, `extract_blocks`, `markdown_to_docs` (Docs API requests), `render_plain_text`, `render_index_doc`, `doc_url`, `utf16_len`; also exports constants `DEFAULT_START_INDEX`, `CODE_BACKGROUND_HEX`, `CODE_BORDER_HEX`, `CODE_FONT_FAMILY`, `CODE_FONT_SIZE_PT`, `NEUTRAL_CODE_LABEL`, `INDEX_DESCRIPTION_SEPARATOR` and re-exports `sanitize_title`, `sanitize_drive_filename`.
- `notes_summary.py`: `extract_short_summary`, `MAX_DESCRIPTION_CHARS`.
- `playlist_url.py`: `parse_playlist_url`.
- `stage_machine.py`: `ALLOWED_TRANSITIONS`, `REGENERATE_FROM`, `can_transition`, `assert_transition`, `last_stable_stage`, `can_fail`, `can_regenerate`, `InvalidTransitionError`.
- `sync_diff.py`: `PlaylistEntry`, `SyncResult` (`has_changes`), `diff_playlist`.

## Invariants
- Depend on `domain.models`, `domain.exceptions` and the standard library; `markdown_to_docs` also imports `domain.interfaces.drive_publisher.IndexDetail`.
- Stage changes must go through `stage_machine`; `assert_transition` raises `InvalidTransitionError` on illegal moves. Same-stage moves are always allowed; `failed` to `queued` is legal only with `is_retry=True`; `REGENERATE_FROM` includes `FAILED`.
- Document names and Drive filenames are always sanitized; empty titles fall back to `FALLBACK_TITLE`.
- Docs request indexes are UTF-16 based (`utf16_len`).
- `safe_diagnostic` keeps only the first line, truncated to 200 characters by default, so raw errors do not leak.

## Side Effects
- `backoff.sleep_for` awaits an asyncio sleep; `compute_delay(attempt, base_seconds, cap_seconds, jitter_ratio=0.2, rng=None)` uses the module-level RNG only when no `rng` is passed, and `iso_after` reads the clock. All other functions are pure.

## Verification
- Run: `pytest`
