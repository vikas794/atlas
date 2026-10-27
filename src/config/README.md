# src/config

## Overview
Layered configuration for Atlas. Values come from `src/configs/config.yaml`, settings saved from the UI (stored in the database) and environment variables (including `.env`).

## Interface
- `settings.AtlasSettings`: frozen dataclass of every setting; `problems()` returns validation messages.
- `settings.load_settings(config_path=None)`: merges layers and raises `SettingsError` when invalid.
- `settings.read_layers(config_path=None)` / `SettingsLayers`: the yaml, db and env layers separately; `merged(db=None)` for what-if validation.
- `settings.get_storage_settings(settings=None)`: absolute `database_path` and `transcripts_dir`.
- `SettingsLayers` also carries `config_name` and `problems`.
- `AtlasSettings.from_yaml` / `from_dict` / `to_loader`, `cors_origin_list`.
- `settings.SettingsError`, `SettingsLoader` (protocol), `repo_root()`, `field_kind(name)`, `check_value`, `FIELD_TYPES`, `ENV_ALIASES` (`NOTES_PROMPT_NAME`), `LOG_LEVELS`, `DEFAULT_CONFIG_RELPATH`.
- Environment override scheme: `ATLAS_<FIELD_NAME>` for every field, the unprefixed names in `ENV_ALIASES`, and the legacy `ATLAS_DB_PATH` for `database_path`.
- `editable.EditableField`, `GROUPS`, `is_editable(key)`, `default_values()`: which settings the UI may edit and whether each applies live or needs a restart. Also `EDITABLE_FIELDS`, `FIELDS_BY_KEY`, `NOT_STORABLE` (`{"database_path"}`).
- Package exports (`__init__`): `AtlasSettings`, `SettingsError`, `SettingsLoader`, `get_storage_settings`, `load_settings`.

## Invariants
- Precedence, lowest to highest: yaml, saved (UI) settings, environment.
- `database_path` is restart-only and is never read from the database it locates.
- `database_path` is in `FIELDS_BY_KEY` but `is_editable("database_path")` is False.
- An empty environment value counts as unset; a malformed env number is collected as a problem in `load_settings`, not skipped.
- Relative paths resolve against the repository root, never the current directory.
- Invalid values are collected and raised together as one readable `SettingsError`, not one at a time.
- Secrets (key env-var names, Google credential and token paths) are not UI-editable.

## Side Effects
- Loads `.env` without overriding real environment variables.
- Reads `config.yaml` and opens the SQLite database read-only to fetch saved settings.
- Logs at debug level when a key falls back to its default.

## Verification
- Run: `uv run pytest -q tests/test_config_env.py tests/test_config_validation.py tests/test_settings_api.py`
