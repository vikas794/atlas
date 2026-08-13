from __future__ import annotations

import hashlib
import os
from pathlib import Path

from src.domain.interfaces.storage import ArtifactStorePort
from src.utils import atomic_write


class ArtifactFileStore(ArtifactStorePort):
    """Filesystem implementation of ArtifactStorePort.

    Stores artifacts under: artifact_root / run_id / kind / {video_id}.{ext}
    """

    def __init__(self, artifact_root: str | Path) -> None:
        self.artifact_root = Path(artifact_root).resolve()

    def _artifact_path(
        self, run_id: str, kind: str, video_id: str, ext: str = "srt"
    ) -> Path:
        return self.artifact_root / run_id / kind / f"{video_id}.{ext}"

    def write_transcript(
        self, run_id: str, video_id: str, language: str, content: str
    ) -> Path:
        path = self._artifact_path(run_id, "transcripts", video_id, language)
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write(str(path), content)
        return path

    def write_summary(self, run_id: str, video_id: str, content: str) -> Path:
        path = self._artifact_path(run_id, "summaries", video_id, "md")
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write(str(path), content)
        return path

    def write_assignment(self, run_id: str, video_id: str, content: str) -> Path:
        path = self._artifact_path(run_id, "assignments", video_id, "json")
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write(str(path), content)
        return path

    def read_transcript(
        self, run_id: str, video_id: str, language: str
    ) -> str | None:
        path = self._artifact_path(run_id, "transcripts", video_id, language)
        if not path.exists():
            return None
        return path.read_text(encoding="utf-8")

    def read_summary(self, run_id: str, video_id: str) -> str | None:
        path = self._artifact_path(run_id, "summaries", video_id, "md")
        if not path.exists():
            return None
        return path.read_text(encoding="utf-8")

    def exists(self, run_id: str, kind: str, video_id: str, ext: str = "srt") -> bool:
        path = self._artifact_path(run_id, kind, video_id, ext)
        return path.exists()

    # Protocol methods (async) - delegate to sync implementations
    async def write_text(self, path: str, content: str) -> str:
        abs_path = self.artifact_root / path
        abs_path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write(str(abs_path), content)
        return hashlib.sha256(content.encode("utf-8")).hexdigest()

    async def write_bytes(self, path: str, content: bytes) -> str:
        abs_path = self.artifact_root / path
        abs_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = f"{abs_path}.tmp-{os.getpid()}"
        with open(tmp_path, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, abs_path)
        return hashlib.sha256(content).hexdigest()

    async def read_text(self, path: str) -> str | None:
        abs_path = self.artifact_root / path
        if not abs_path.exists():
            return None
        return abs_path.read_text(encoding="utf-8")

    async def read_bytes(self, path: str) -> bytes | None:
        abs_path = self.artifact_root / path
        if not abs_path.exists():
            return None
        return abs_path.read_bytes()

    async def delete(self, path: str) -> None:
        abs_path = self.artifact_root / path
        if abs_path.exists():
            abs_path.unlink()

    async def async_exists(self, path: str) -> bool:
        """Async existence check for ArtifactStorePort compliance."""
        abs_path = self.artifact_root / path
        return abs_path.exists()

    async def file_size(self, path: str) -> int | None:
        abs_path = self.artifact_root / path
        if not abs_path.exists():
            return None
        return abs_path.stat().st_size
