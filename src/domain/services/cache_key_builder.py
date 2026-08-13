from __future__ import annotations

import hashlib

from src.domain.interfaces.cache import CacheKey


class CacheKeyBuilder:
    """Pure domain logic for building cache keys."""

    VERSION = "v2"

    @staticmethod
    def _make_key(kind: str, *parts: object) -> CacheKey:
        """Build a cache key from a kind and ordered parts."""
        payload = "|".join(str(p) if p is not None else "" for p in parts)
        digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
        return CacheKey(key=digest, kind=kind)

    @classmethod
    def transcript_key(cls, video_id: str, language: str, content_hash: str) -> CacheKey:
        return cls._make_key(
            f"transcript:{cls.VERSION}",
            video_id,
            language,
            content_hash,
        )

    @classmethod
    def summary_key(
        cls, video_id: str, transcript_hash: str, prompt_version: str, model: str
    ) -> CacheKey:
        return cls._make_key(
            f"summary:{cls.VERSION}",
            video_id,
            transcript_hash,
            prompt_version,
            model,
        )

    @classmethod
    def comparison_key(
        cls,
        run_id: str,
        input_hash: str,
        prompt_version: str,
        model: str,
        use_ai_insights: bool,
    ) -> CacheKey:
        return cls._make_key(
            f"comparison:{cls.VERSION}",
            run_id,
            input_hash,
            prompt_version,
            model,
            str(use_ai_insights).lower(),
        )

    @classmethod
    def assignment_key(
        cls, video_id: str, summary_hash: str, prompt_version: str, model: str
    ) -> CacheKey:
        return cls._make_key(
            f"assignment:{cls.VERSION}",
            video_id,
            summary_hash,
            prompt_version,
            model,
        )

    @classmethod
    def quiz_key(
        cls, video_id: str, transcript_hash: str, prompt_version: str, model: str
    ) -> CacheKey:
        return cls._make_key(
            f"quiz:{cls.VERSION}",
            video_id,
            transcript_hash,
            prompt_version,
            model,
        )

    @classmethod
    def search_key(
        cls, normalized_query: str, max_videos: int, transcript_language: str
    ) -> CacheKey:
        return cls._make_key(
            f"search:{cls.VERSION}",
            normalized_query,
            max_videos,
            transcript_language,
        )
