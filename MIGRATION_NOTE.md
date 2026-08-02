# Python 3.13 Migration Note

## Summary

Atlas now uses Python 3.13.13 and `uv` for reproducible dependency management. `pyproject.toml` declares direct dependencies and `uv.lock` is the authoritative resolved dependency set.

## Developer Workflow

```powershell
# Application dependencies
uv sync --locked --no-dev

# Development dependencies and checks
uv sync --locked --dev
uv run ruff check .
uv run pytest
uv pip check
```

The Windows launcher validates `.venv` and recreates it when its interpreter is not Python 3.13.13. It then runs `uv sync --locked --no-dev` before starting the application.

## Dependency Groups

- Runtime dependencies are defined in `[project.dependencies]`.
- The optional legacy Gradio UI is installed with `uv sync --locked --extra legacy-gradio`.
- Test and lint dependencies are defined only in `[dependency-groups].dev`.

`requirements.txt` and `requirements-legacy.txt` were intentionally retired. Do not use `pip install -r ...`; use the locked `uv` commands above.

## Data Compatibility

Existing LanceDB data is not changed by this migration. Back up `storage/` before future LanceDB upgrades and rebuild indexes if a later LanceDB release requires it.

## Rollback

To roll back, restore the previous project revision, remove `.venv`, and follow that revision's documented setup workflow:

```powershell
git checkout <previous-commit>
Remove-Item -Recurse -Force .venv
```
