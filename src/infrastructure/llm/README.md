# llm

## Overview
LLM notes-generation infrastructure. A single LiteLLM adapter generates notes through the model registry; importing the package pulls in no provider SDK (`litellm` is imported lazily).

## Interface
- `build_notes_generator(settings_loader, *, registry, usage_ledger=None)` (`factory.py`), plus re-exports `GeneratedNotes`, `NotesGeneratorPort`.
- `LiteLLMNotesGenerator` (`litellm_notes.py`): `specs`, `provider`, `model`, `prompt_version`, async `generate_notes(..., prompt_template=None)`; `NOTES_OPERATION`. The constructor takes an optional `completion` callable (`litellm.acompletion`-shaped; tests inject a double). A one-off `prompt_template` tags the summary `custom`.
- `notes_base.py`: `render_prompt`, `validate_notes`, `estimate_tokens`, `check_input_size`, `check_finish_reason`, `classify_provider_exception`, `redact_secrets`, `request_timeout_seconds`, `DEFAULT_REQUEST_TIMEOUT_SECONDS` (300s when `notes_request_timeout_seconds` is unset), `EXPECTED_SECTIONS` (Notes, Key Points, Important Details, Takeaways), `MIN_SECTIONS_FOUND`, `NotesPromptSource`, placeholders and `MIN_NOTES_CHARS`.
- `SettingsLoader` protocol (`base.py`); `ModelSpec` and default model/base-URL constants (`models.py`); `catalog_models(provider)` (`catalog.py`); `response_cost`, `call_cost` (`pricing.py`; cost resolves from the response's own cost, then `litellm.cost_per_token` on the full name and path suffixes, then a bare-name table match, else `0.0`).
- Subpackage: [registry](./registry/README.md).

## Invariants
- The adapter does not retry a provider call (it does rotate across a model's usable keys and walk the fallback chain on failure); `ProcessVideoUseCase` owns persisted retries and backoff.
- Prompt templates use the `{{Title}}` and `{{Transcript}}` placeholders; notes must meet a minimum length and contain at least 2 expected sections.
- Secrets are redacted from error text; provider exceptions map to domain failure types.

## Side Effects
- Network calls to LLM providers via LiteLLM; reads prompt files from disk (`notes_prompt_name`, default `notes_v1_2.yaml`, under `prompts_base_dir`, default `src/prompts`; the version is the file's `version` key, else a sha256 prefix); records usage via the ledger and registry.

## Verification
- Run: `pytest` (from repo root)
