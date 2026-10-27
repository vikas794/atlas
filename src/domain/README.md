# domain

## Overview
Innermost layer: entities, ports, pure services and exceptions. Contains no framework, database, or network code. `__init__.py` re-exports a few common symbols.

## Interface
- `__init__.py` re-exports `DomainError`, `ProviderError`, `StorageError`, `TranscriptProviderPort`, `TranscriptResult`, `UsageLedgerPort`, `UsageRecord`, the usage aggregates and `TimeRange`; other public symbols live in the subpackages below.

## Invariants
- Must not import from `application`, `src.config`, infrastructure, or web layers (`tests/test_architecture.py` checks this); it does not import `src.shared` either.

## Side Effects
- None.

## Subdirectories & Boundaries
- exceptions: [./exceptions/README.md](./exceptions/README.md) - `DomainError` hierarchy.
- interfaces: [./interfaces/README.md](./interfaces/README.md) - Protocol ports and value objects for repositories, providers, Drive, notes, usage ledger.
- models: [./models/README.md](./models/README.md) - `Playlist`, `Video`, `Stage`, failure types.
- services: [./services/README.md](./services/README.md) - stage machine, classifier, naming, Markdown-to-Docs, diffing.

## Verification
- Run: `pytest`
