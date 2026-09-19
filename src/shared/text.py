"""Small, dependency-free text/file helpers shared across layers.

These were previously duplicated between ``src/utils.py`` (legacy config
module) and the old backend artifacts helper (now
``src/infrastructure/storage/hashing.py``). This is the single canonical
home for the pieces that have nothing to do with configuration loading.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any


def sha256_text(content: str) -> str:
    """Return the SHA-256 hex digest of a string's UTF-8 bytes."""
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def atomic_write(path: str | Path, content: str | bytes, encoding: str = "utf-8") -> None:
    """Write content to a temp file then atomically replace ``path``."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f"{path.name}.tmp-{os.getpid()}")
    data = content if isinstance(content, bytes) else content.encode(encoding)
    with open(tmp_path, "wb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp_path, path)


def ensure_output_folder(folder_path: str) -> str:
    """Ensure output folder exists and return absolute path."""
    abs_path = os.path.abspath(folder_path)
    os.makedirs(abs_path, exist_ok=True)
    return abs_path


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
