from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

from src.application.dto.quiz import QuizGenerationInput, QuizGenerationOutput, VideoQuizResult
from src.application.ports.provider_ports import (
    RunRepositoryPort,
    QuizGeneratorPort,
    TranscriptProviderPort,
    CachePort,
    UsageLedgerPort,
    ArtifactStorePort,
)
from src.domain.exceptions import DomainError, ProviderError
from src.infrastructure.llm.gemini.adapter import GeminiQuizProvider
from src.infrastructure.google.drive import GoogleDriveExporter
from src.infrastructure.transcript.ytdlp.provider import YtDlpTranscriptProvider
from src.infrastructure.llm.base import SettingsLoader
from src.utils import get_config, get_worker_count, ensure_output_folder


class GenerateQuizUseCase:
    """Orchestrates quiz generation from a YouTube playlist.

    This use case formalizes the quiz orchestration previously
    embedded in backend.services.quiz_service.QuizService.process_playlist().
    """

    def __init__(
        self,
        run_repository: RunRepositoryPort,
        cache: CachePort,
        settings: SettingsLoader,
        usage_ledger: Optional[UsageLedgerPort] = None,
    ) -> None:
        self._repo = run_repository
        self._cache = cache
        self._settings = settings
        self._usage = usage_ledger

    async def execute(self, input: QuizGenerationInput) -> QuizGenerationOutput:
        gemini_key = input.gemini_api_key if not input.use_env_keys else self._settings("environment.gemini_api_key_env", "GEMINI_API_KEY")
        if not gemini_key:
            raise DomainError("Gemini API Key is required.")

        gemini_key = gemini_key.strip()
        await self._validate_gemini_credentials(gemini_key)

        playlist_id = self._extract_playlist_id(input.playlist_url)
        playlist_title = await self._fetch_playlist_title(playlist_id)

        def report(stage: str, message: str, **details: Any) -> None:
            if input.progress_callback:
                input.progress_callback({"stage": stage, "message": message, **details})

        report("preparing", "Checking Google Drive access and preparing your quiz workspace.")

        drive_exporter = GoogleDriveExporter()

        max_videos = input.max_videos or get_config("playlist_quiz.max_videos", 50)
        videos = await self._fetch_playlist_videos(playlist_id, max_videos)
        report("playlist", f"Found {len(videos)} video{'s' if len(videos) != 1 else ''} in {playlist_title}.", total=len(videos))

        transcript_folder = ensure_output_folder(get_config("playlist_quiz.output_folder", "quiz_output"))
        report("transcripts", "Fetching transcripts for the playlist videos.", total=len(videos))

        transcript_provider = YtDlpTranscriptProvider(
            settings=self._settings,
            usage_ledger=self._usage,
        )
        urls = [f"https://www.youtube.com/watch?v={v['video_id']}" for v in videos]
        transcript_results = await transcript_provider.fetch_transcripts(urls)

        available_transcripts = sum(
            r.raw_srt != "" for r in transcript_results
        )
        report(
            "generating",
            f"Found transcripts for {available_transcripts} of {len(videos)} videos. Generating available quizzes and saving them to Drive.",
            current=available_transcripts,
            total=len(videos),
        )

        folder_id = None
        folder_url = None
        if available_transcripts:
            folder_id = drive_exporter.create_folder(playlist_title)
            folder_url = f"https://drive.google.com/drive/folders/{folder_id}"
        else:
            report("finalizing", "No transcripts were available; skipping Google Drive folder creation and quiz generation.", total=len(videos))
            return QuizGenerationOutput(
                status="completed",
                playlist_title=playlist_title,
                total_videos=len(videos),
                processed=0,
                failed=len(videos),
                drive_folder_url=None,
                video_results=[],
            )

        results = []
        quiz_provider = GeminiQuizProvider(
            settings=self._settings,
            usage_ledger=self._usage,
        )

        for i, (video, transcript_result) in enumerate(zip(videos, transcript_results)):
            pos = video.get("position", i) + 1
            title_prefix = f"{pos}. {video['title']}"
            video_url = f"https://www.youtube.com/watch?v={video['video_id']}"

            if not transcript_result.raw_srt:
                status = f"Skipped: {transcript_result.error or 'Transcript was not downloaded for this video.'}"
                results.append(VideoQuizResult(
                    position=pos,
                    video_id=video["video_id"],
                    title=video["title"],
                    status=status,
                    doc_url=None,
                ))
                report("generating", f"Skipped quiz {pos} of {len(videos)}: {status}", current=pos, total=len(videos), completed=pos)
                continue

            report("generating", f"Creating quiz {pos} of {len(videos)}: {video['title']}", current=pos, total=len(videos), title=video["title"])

            try:
                from src.domain.interfaces.quiz_generator import QuizContext, TranscriptRef
                from src.domain.models.transcript import TranscriptContent
                from src.domain.models.video import VideoId

                transcript_ref = TranscriptRef(
                    video_id=VideoId(value=video["video_id"]),
                    language="en",
                    content_hash=transcript_result.content_hash,
                    artifact_path=transcript_result.artifact_path,
                    available=bool(transcript_result.raw_srt),
                )

                quiz_context = QuizContext(
                    playlist_url=input.playlist_url,
                    gemini_api_key=gemini_key,
                    max_videos=input.max_videos,
                    model=self._settings("api.gemini.model", "gemini-3.6-flash"),
                )

                quiz_result = await quiz_provider.generate_quiz(transcript_ref, video["title"], quiz_context)

                if quiz_result and quiz_result.success and quiz_result.content:
                    doc_id = drive_exporter.create_doc_in_folder(title=title_prefix, content=quiz_result.content, folder_id=folder_id)
                    doc_url = f"https://docs.google.com/document/d/{doc_id}/edit"
                    status = "Generated & Uploaded"
                else:
                    doc_url = None
                    status = f"Failed: {quiz_result.error if hasattr(quiz_result, 'error') and quiz_result.error else 'Quiz generation did not return any content.'}"

            except Exception as e:
                doc_url = None
                status = f"Failed: {str(e)}"

            results.append(VideoQuizResult(
                position=pos,
                video_id=video["video_id"],
                title=video["title"],
                status=status,
                doc_url=doc_url,
            ))
            report("generating", f"Finished quiz {pos} of {len(videos)}.", current=pos, total=len(videos), completed=pos)

            if i < len(videos) - 1:
                delay = get_config("playlist_quiz.delay_between_requests", 2)
                import asyncio
                await asyncio.sleep(delay)

        report("finalizing", "Finalizing your results and Drive links.", total=len(videos))

        processed = sum(1 for r in results if r.doc_url is not None)
        failed = sum(1 for r in results if r.doc_url is None)

        return QuizGenerationOutput(
            status="completed",
            playlist_title=playlist_title,
            total_videos=len(videos),
            processed=processed,
            failed=failed,
            drive_folder_url=folder_url,
            video_results=results,
        )

    async def _validate_gemini_credentials(self, api_key: str) -> None:
        from google import genai

        if not api_key.startswith("AIza"):
            raise DomainError(
                "GEMINI_API_KEY must be a Google AI Studio API key (normally starting with 'AIza')."
            )

        model_name = self._settings("api.gemini.model", "gemini-3.6-flash")
        try:
            client = genai.Client(api_key=api_key)
            client.models.get(model=model_name)
        except Exception as error:
            detail = str(error)
            normalized_detail = detail.lower()
            if "api_key_invalid" in normalized_detail or "api key not valid" in normalized_detail:
                raise DomainError("Gemini API key is invalid. Update GEMINI_API_KEY and try again.") from error
            raise DomainError("Unable to verify Gemini access before starting the playlist.") from error

    def _extract_playlist_id(self, url: str) -> str:
        import re
        match = re.search(r"[?&]list=([^#&?]+)", url)
        if match:
            return match.group(1)
        return url

    async def _fetch_playlist_title(self, playlist_id: str) -> str:
        from src.infrastructure.youtube.playlist import YouTubePlaylistProvider
        provider = YouTubePlaylistProvider()
        return provider.get_playlist_title(playlist_id)

    async def _fetch_playlist_videos(self, playlist_id: str, max_videos: int) -> List[Dict[str, Any]]:
        from src.infrastructure.youtube.playlist import YouTubePlaylistProvider
        provider = YouTubePlaylistProvider()
        videos = provider.fetch_playlist_videos(playlist_id, max_videos=max_videos)
        return videos