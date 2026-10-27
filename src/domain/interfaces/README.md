# domain/interfaces

## Overview
Ports (`typing.Protocol` classes) and their value objects that the domain and application layers depend on. Infrastructure adapters implement these; nothing here performs I/O itself.

## Interface
- `drive_publisher.py`: `DrivePublisherPort` (`ensure_authorized`, `publish_playlist`), `PublishProgress` callback protocol, `PublishEntry`, `IndexDetail`, `PublishedDoc`, `PublishResult`, `DriveAuthorizationRequired`, `DriveFolderMissing`.
- `notes_generator.py`: `NotesGeneratorPort.generate_notes`, `GeneratedNotes`, `missing_placeholders`, constants `CUSTOM_PROMPT_VERSION`, `PROMPT_PLACEHOLDERS`, `MAX_CUSTOM_PROMPT_CHARS`.
- `playlist_provider.py`: `PlaylistProviderPort` (`get_playlist_title`, `fetch_playlist_videos`).
- `playlist_repository.py`: `PlaylistRepositoryPort` - persistence for playlists, videos, transcripts, summaries (create/get/list/update/delete, publish begin/finish, stage updates, retries, approve, regenerate, recovery queries), plus counts and lookups (`count_playlists`, `count_videos`, `max_position`, `count_by_stage`, `count_published`, `edited_video_ids`), `copy_summary`, and Drive/index setters (`set_pad_width`, `set_index_digest`, `set_drive_folder`, `set_drive_index_doc`).
- `transcript_provider.py`: `TranscriptProviderPort` (`fetch_transcript`, `fetch_transcripts`), `TranscriptResult`.
- `usage_ledger.py`: `UsageLedgerPort` (`record_usage`, `get_aggregate`, `get_by_provider`, `get_by_operation`, `get_cache_stats`), `UsageRecord` (optional `run_id`, `video_id`), aggregates (`UsageAggregate`, `ProviderAggregate`, `OperationAggregate`, `CacheAggregate`), `TimeRange`.

## Invariants
- Depend on `domain.models`, `domain.exceptions`, the standard library, and (`playlist_provider`, `playlist_repository`) `domain.services.sync_diff.PlaylistEntry`.
- Changing a port signature requires updating every adapter and use case that consumes it.
- Custom notes prompts must contain every placeholder in `PROMPT_PLACEHOLDERS` and stay within `MAX_CUSTOM_PROMPT_CHARS`; the port does not enforce this, `application/use_cases/playlist/actions.py::_checked_prompt` does.

## Side Effects
- None at definition time; implementations perform database, network, and Drive I/O.

## Verification
- Run: `pytest`
