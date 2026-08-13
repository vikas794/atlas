from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable


class RunHashComputer:
    """Pure domain logic for computing stable state hashes.

    Logic is copied from backend/storage/repository.py:_state_hash
    and related methods.
    """

    @staticmethod
    def _stable_json_dumps(obj) -> str:
        """Deterministic JSON serialization used for content and state hashing."""
        return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"))

    @staticmethod
    def _sha256_text(content: str) -> str:
        return hashlib.sha256(content.encode("utf-8")).hexdigest()

    @classmethod
    def state_hash(cls, items: Iterable[tuple[str, str | None]]) -> str:
        """Stable hash of an ordered list of (key, value) pairs."""
        payload = cls._stable_json_dumps(sorted((key, value or "") for key, value in items))
        return cls._sha256_text(payload)

    @classmethod
    def videos_state_hash(cls, videos: list[dict]) -> str:
        """Hash of video IDs and titles."""
        return cls.state_hash((v.get("video_id", ""), v.get("title", "")) for v in videos)

    @classmethod
    def transcripts_state_hash(cls, transcripts: list[dict]) -> str:
        """Hash of succeeded transcript video_ids and content_hashes."""
        return cls.state_hash(
            (row["video_id"], row["content_hash"])
            for row in transcripts
            if row.get("status") == "succeeded" and row.get("content_hash")
        )

    @classmethod
    def summaries_state_hash(cls, summaries: list[dict]) -> str:
        """Hash of succeeded summary video_ids and content_hashes."""
        return cls.state_hash(
            (row["video_id"], row["content_hash"])
            for row in summaries
            if row.get("status") == "succeeded" and row.get("content_hash")
        )

    @classmethod
    def derived_input_hash(
        cls, transcripts_hash: str | None, summaries_hash: str | None
    ) -> str:
        """Combined input identity for comparisons/assignments.

        Both depend on summaries and, transitively, the transcripts those summaries
        were built from — so refreshing either invalidates them.
        """
        return cls.state_hash(
            (
                ("summaries", summaries_hash or ""),
                ("transcripts", transcripts_hash or ""),
            )
        )
