from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Any

import yaml


class PromptRegistry:
    """Loads and caches prompt templates from YAML files.

    Each prompt file must contain a 'prompt' key (string) and optionally
    'system_prompt' and 'user_prompt_template' for assignment-style prompts.
    """

    def __init__(self, base_dir: str | Path, settings_loader: Any = None) -> None:
        self._base_dir = Path(base_dir)
        self._settings = settings_loader
        self._cache: dict[str, dict[str, Any]] = {}

    def _resolve_path(self, prompt_name: str | None) -> Path:
        """Resolve prompt file path relative to base_dir."""
        if prompt_name is None:
            prompt_name = self._get_default_summarizer()
        return (self._base_dir / prompt_name).resolve()

    def _get_default_summarizer(self) -> str:
        """Get default summarizer prompt from settings or fallback."""
        if self._settings:
            return self._settings("prompts.default_summarizer", "summarizer_youtube_v2.yaml")
        return "summarizer_youtube_v2.yaml"

    def load(self, prompt_name: str | None = None) -> dict[str, Any]:
        """Load a prompt YAML file and return parsed dict.

        Returns dict with at least 'prompt' key, optionally 'system_prompt',
        'user_prompt_template', and 'version'.
        """
        path = self._resolve_path(prompt_name)

        if path in self._cache:
            return self._cache[path]

        if not path.exists():
            raise FileNotFoundError(f"Prompt file not found: {path}")

        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}

        if "prompt" not in data and "system_prompt" not in data:
            raise KeyError(f"Prompt file {path!r} missing required 'prompt' or 'system_prompt' key")

        self._cache[path] = data
        return data

    def get_prompt(self, prompt_name: str | None = None) -> str:
        """Get the main prompt string from a prompt file."""
        return self.load(prompt_name)["prompt"]

    def get_assignment_prompts(self, prompt_name: str | None = None) -> dict[str, str]:
        """Get both system_prompt and user_prompt_template for assignments."""
        data = self.load(prompt_name or "assignment_generator.yaml")
        return {
            "system_prompt": data["system_prompt"],
            "user_prompt_template": data["user_prompt_template"],
        }

    def get_version(self, prompt_name: str | None = None) -> str:
        """Get the version string from a prompt file (or content hash)."""
        data = self.load(prompt_name)
        if "version" in data:
            return str(data["version"])
        # Fallback: hash of the prompt content
        prompt_text = data.get("prompt") or data.get("system_prompt", "")
        return hashlib.sha256(prompt_text.encode("utf-8")).hexdigest()[:8]

    def clear_cache(self) -> None:
        """Clear the prompt cache."""
        self._cache.clear()


def get_prompt_path(prompt_name: str | None = None, base_dir: str | Path | None = None) -> str:
    """Get absolute path to a prompt file (legacy compatibility).

    Args:
        prompt_name: Name of the prompt file. If None, uses default from settings.
        base_dir: Base directory for prompts. If None, uses default from settings.

    Returns:
        Absolute path to the prompt file.
    """
    if base_dir is None:
        base_dir = Path(__file__).resolve().parents[2] / "src" / "prompts"
    else:
        base_dir = Path(base_dir)

    if prompt_name is None:
        # Try to load from config
        try:
            from src.config import load_settings
            settings = load_settings()
            prompt_name = settings.summarizer_prompt_name
        except Exception:
            prompt_name = "summarizer_youtube_v2.yaml"

    return str((base_dir / prompt_name).resolve())


def load_prompt_template(prompt_name: str | None = None, base_dir: str | Path | None = None) -> str:
    """Load a prompt YAML file and return its 'prompt' field (legacy compatibility)."""
    path = get_prompt_path(prompt_name, base_dir)
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    if "prompt" not in data:
        raise KeyError(f"Prompt file {path!r} missing required 'prompt' key")
    return data["prompt"]


def load_assignment_prompts(prompt_name: str | None = None, base_dir: str | Path | None = None) -> dict[str, str]:
    """Load both 'system_prompt' and 'user_prompt_template' fields (legacy compatibility)."""
    path = get_prompt_path(prompt_name or "assignment_generator.yaml", base_dir)
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    for key in ("system_prompt", "user_prompt_template"):
        if key not in data:
            raise KeyError(f"Prompt file {path!r} missing required {key!r} key")
    return {"system_prompt": data["system_prompt"], "user_prompt_template": data["user_prompt_template"]}