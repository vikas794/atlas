# domain/exceptions

## Overview
Framework-free exception hierarchy shared by all layers. Domain errors derive from `DomainError`, so outer layers can translate errors without importing infrastructure types. `PipelineError`, `TranscriptError` and `NotesGenerationError` (in `models/playlist.py`) derive from plain `Exception`.

## Interface
- `DomainError` - base class.
- `ProviderError(message, provider=None)` (exposes `.provider`), `CacheError`, `StorageError` - external-system failures.
- `ConfigurationError` - invalid configuration; never transient (retrying cannot fix it). The HTTP layer maps it to `503 CONFIG_MISSING`. `NotesGeneratorConfigError(ConfigurationError, ValueError)` covers notes-generator setup problems; the pipeline records it as a permanent `config_error`.
- `NotFoundError`, `ConflictError`, `StaleRunError` - lookup, state-conflict and stale-run conditions.
- `InvalidTransitionError` (`services/stage_machine.py`), `DriveAuthorizationRequired` and `DriveFolderMissing` (`interfaces/drive_publisher.py`) are `DomainError` subclasses defined elsewhere.

## Invariants
- All classes defined here inherit from `DomainError`; no dependency on infrastructure, web, or database packages.
- Subclass semantics are part of the API contract (callers map them to responses); do not repurpose them.

## Side Effects
- None; pure class definitions.

## Verification
- Run: `pytest`
