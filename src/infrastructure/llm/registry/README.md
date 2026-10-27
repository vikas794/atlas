# llm/registry

## Overview
Database-backed model registry: models, API keys, one ordered fallback chain, per-key cooldowns, rate-limit and token usage ledgers, and key health checks.

## Interface
- `ModelRegistry(repo, *, clock=time.time)` (injectable clock), `Candidate` (`service.py`): `candidates(estimated_tokens)`, `usable_keys`, `begin_attempt`, `record_success`, `record_failure`, `cooldowns_by_key`, `rate_limit_usage`, `token_usage`, `check_key`, `check_all`. Constants `RATE_LIMIT_LADDER`, `AUTO_DISABLE_AFTER`, `USABLE_STATUSES`, cooldown seconds.
- `RegistryRepo(db_path)`, `ModelRow`, `KeyRow` (`repo.py`): CRUD for models and keys, `replace_chain`, key health, cooldown and usage tables, meta and `saved_settings`. `ALL_MODELS = "*"`.
- `seed_registry(registry)` (`seed.py`): one-time seeding of defaults.
- `PROVIDERS`, `ProviderInfo`, `known_providers()`, `provider_info(name)`, `litellm_model_for(provider, model_id)`, `mask_key(value)` (`providers.py`).
- `fetch_provider_models(provider, key, api_base)`, `probe_key(...)`, `ProviderQueryError`, `GEMINI_BASE` (`health.py`). `registry/__init__.py` exports only `Candidate`, `ModelRegistry`, `RegistryRepo`, `seed_registry`.

## Invariants
- Rate-limit cooldowns climb the ladder (120s, 10m, 1h, 24h) and cap at 24h. The ladder continues only if the previous cooldown was `heuristic` and set within `LADDER_MEMORY_SECONDS` (24h); a provider `retry_after` makes the cooldown authoritative (the larger value wins).
- A key is auto-disabled after `AUTO_DISABLE_AFTER` (3) provider rejections; rate-limit failures never disable it, credit failures set a 24h all-models cooldown, and a rejection sets a 300s cooldown. Only `healthy`, `unknown` and `rate_limited` keys are usable.
- Key values are masked when exposed.
- `known_providers()` / `provider_info()` also cover every OpenAI-compatible LiteLLM provider except `github_copilot`, `azure_ai`, `chatgpt`. The slug check for model ids lives in `schemas/llm.py`; `providers.py` has a private `_SLUG` for discovery.
- Seeding runs once (guarded by a meta flag).

## Side Effects
- SQLite reads/writes; network calls to provider APIs for model listing and key probes (15s timeout).

## Verification
- Run: `pytest` (from repo root)
