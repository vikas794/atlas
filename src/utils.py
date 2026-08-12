"""Utility functions for the Atlas project.

This module contains utility functions for configuration loading, file operations,
and common functionality used across the project.
"""

import json
import logging
import os
import re
from pathlib import Path
from typing import Any, Dict, Optional

import yaml

# Global config cache
_config: Optional[Dict[str, Any]] = None
_config_path: Optional[str] = None


def load_config(
    config_path: Optional[str] = None, force_reload: bool = False
) -> Dict[str, Any]:
    """Load project configuration from YAML file.

    Args:
        config_path (Optional[str]): Path to configuration file.
            If None, uses default config.yaml in src/configs/
        force_reload (bool): Force reload even if config is already loaded.

    Returns:
        Dict[str, Any]: Loaded configuration dictionary.

    Raises:
        FileNotFoundError: If config file doesn't exist.
        yaml.YAMLError: If config file is invalid YAML.
    """
    global _config, _config_path

    # Resolve config path
    if config_path is None:
        current_dir = Path(__file__).parent
        config_path = str((current_dir / "configs" / "config.yaml").absolute())
    else:
        config_path = os.path.abspath(config_path)

    # Return cached config if available and not forcing reload
    if _config is not None and _config_path == config_path and not force_reload:
        return _config

    if not os.path.exists(config_path):
        raise FileNotFoundError(f"Configuration file not found: {config_path}")

    try:
        with open(config_path, "r", encoding="utf-8") as file:
            _config = yaml.safe_load(file)
            _config_path = config_path
            return _config
    except yaml.YAMLError as e:
        raise yaml.YAMLError(f"Error parsing config file {config_path}: {e}")


def get_config(key_path: str, default: Any = None) -> Any:
    """Get a configuration value using dot notation.

    Args:
        key_path (str): Dot-separated path to the config value (e.g., 'api.openai.model').
        default (Any): Default value if key is not found.

    Returns:
        Any: Configuration value or default.

    Examples:
        >>> get_config('api.openai.model')
        'gpt-5'
        >>> get_config('api.openai.timeout', 60)
        120
    """
    if _config is None:
        load_config()

    keys = key_path.split(".")
    value = _config

    try:
        for key in keys:
            value = value[key]
        return value
    except (KeyError, TypeError):
        return default


def get_worker_count(num_workers: Optional[int] = None) -> int:
    """Determine the number of workers based on config and parameters.

    Args:
        num_workers (Optional[int]): User-specified number of workers.

    Returns:
        int: Number of workers to use (0 means sequential processing).
    """
    if num_workers is not None:
        return max(0, num_workers)

    workers_config = get_config("processing.workers", {})

    if not workers_config.get("auto_detect", True):
        return workers_config.get("min_workers", 2)

    # Auto-detect based on CPU count
    cpu_count = os.cpu_count() or 1
    cpu_ratio = workers_config.get("cpu_ratio", 0.5)
    min_workers = workers_config.get("min_workers", 2)
    max_workers = workers_config.get("max_workers", 16)

    auto_workers = max(min_workers, min(max_workers, int(cpu_count * cpu_ratio)))
    return auto_workers


def get_prompt_path(prompt_name: Optional[str] = None) -> str:
    """Get the full path to a prompt template file.

    Args:
        prompt_name (Optional[str]): Name of the prompt file.
            If None, uses default summarizer from config.

    Returns:
        str: Absolute path to the prompt file.
    """
    prompts_config = get_config("paths.prompts", {})
    base_dir = prompts_config.get("base_dir", "src/prompts")

    if prompt_name is None:
        prompt_name = prompts_config.get(
            "default_summarizer", "summarizer_youtube_v2.yaml"
        )

    # Resolve relative to project root
    current_dir = Path(__file__).parent.parent  # Go up from src/ to project root
    prompt_path = current_dir / base_dir / prompt_name
    return str(prompt_path.absolute())


def setup_logging() -> None:
    """Setup logging based on configuration."""
    log_config = get_config("logging", {})
    level = getattr(logging, log_config.get("level", "INFO").upper())
    format_str = log_config.get("format", "[%(levelname)s] %(message)s")

    # Configure basic logging
    logging.basicConfig(level=level, format=format_str, force=True)

    # Setup file logging if enabled
    if log_config.get("file_logging", False):
        log_file = log_config.get("log_file", "logs/atlas.log")
        os.makedirs(os.path.dirname(log_file), exist_ok=True)

        file_handler = logging.FileHandler(log_file)
        file_handler.setLevel(level)
        file_handler.setFormatter(logging.Formatter(format_str))

        # Add to root logger
        logging.getLogger().addHandler(file_handler)


def ensure_output_folder(folder_path: str) -> str:
    """Ensure output folder exists and return absolute path.

    Args:
        folder_path (str): Path to the folder.

    Returns:
        str: Absolute path to the folder.
    """
    abs_path = os.path.abspath(folder_path)
    os.makedirs(abs_path, exist_ok=True)
    return abs_path


def sha256_text(content: str) -> str:
    """Return the SHA-256 hex digest of a string's UTF-8 bytes."""
    import hashlib

    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def atomic_write(path: str, content: str, encoding: str = "utf-8") -> None:
    """Write content to a temp file then atomically replace ``path``."""
    import os

    abs_path = os.path.abspath(path)
    parent = os.path.dirname(abs_path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    tmp_path = f"{abs_path}.tmp-{os.getpid()}"
    with open(tmp_path, "w", encoding=encoding) as handle:
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp_path, abs_path)


def load_json_with_recovery(raw_content: str) -> dict[str, Any]:
    """Parse JSON, repairing unescaped newlines/tabs inside string values.

    Fallback for LLM-produced JSON that is not strictly valid.
    """
    try:
        return json.loads(raw_content)
    except json.JSONDecodeError:
        fixed_content: list[str] = []
        in_string = False
        escape_next = False

        for char in raw_content:
            if escape_next:
                fixed_content.append(char)
                escape_next = False
            elif char == "\\":
                fixed_content.append(char)
                escape_next = True
            elif char == '"':
                fixed_content.append(char)
                in_string = not in_string
            elif in_string and char == "\n":
                fixed_content.append("\\n")
            elif in_string and char == "\r":
                fixed_content.append("\\r")
            elif in_string and char == "\t":
                fixed_content.append("\\t")
            else:
                fixed_content.append(char)

        return json.loads("".join(fixed_content))


def clean_srt_content(raw_srt: str) -> str:
    """Strip SRT cues/timestamps and group the remaining text into paragraphs."""
    clean_content = re.sub(
        r"\d+\n\d{2}:\d{2}:\d{2},\d{3} --> \d{2}:\d{2}:\d{2},\d{3}\n",
        "",
        raw_srt,
    )
    clean_content = re.sub(r"^\d+$", "", clean_content, flags=re.MULTILINE)
    clean_content = re.sub(r"\n\s*\n", "\n", clean_content)
    clean_content = clean_content.strip()

    if not clean_content:
        return ""

    sentences = clean_content.replace("\n", " ").split(". ")
    paragraphs: list[str] = []
    current_paragraph: list[str] = []

    for index, sentence in enumerate(sentences):
        sentence = sentence.strip()
        if not sentence:
            continue

        if index < len(sentences) - 1 and not sentence.endswith("."):
            sentence += "."
        current_paragraph.append(sentence)

        if (index + 1) % 4 == 0:
            paragraphs.append(" ".join(current_paragraph))
            current_paragraph = []

    if current_paragraph:
        paragraphs.append(" ".join(current_paragraph))

    return "\n\n".join(paragraphs).strip()
