import os
from typing import Any, Callable, Dict, Optional
from fastapi import HTTPException

from backend.schemas.quiz import PlaylistQuizRequest, PlaylistQuizStatusResponse
from backend.services.artifact_readers import REPO_ROOT

class QuizService:
    def __init__(self):
        pass

    def check_drive_auth(self) -> bool:
        """Check if Drive credentials/tokens are in place."""
        has_token = os.path.exists('token.json')
        has_creds = os.path.exists('credentials.json')
        return has_token or has_creds

    def process_playlist(
        self,
        request: PlaylistQuizRequest,
        progress_callback: Optional[Callable[[Dict[str, Any]], None]] = None,
    ) -> PlaylistQuizStatusResponse:
        gemini_key = request.gemini_api_key if not request.use_env_keys else os.getenv("GEMINI_API_KEY")
        if not gemini_key:
            raise HTTPException(
                status_code=400,
                detail="Gemini API Key is required."
            )

        # Update environment if needed so internal modules can use it
        if not request.use_env_keys and request.gemini_api_key:
             os.environ["GEMINI_API_KEY"] = request.gemini_api_key

        try:
            from src.playlist_quiz_generator import PlaylistQuizPipeline

            pipeline = PlaylistQuizPipeline(gemini_api_key=gemini_key)
            result = pipeline.run(
                request.playlist_url,
                max_videos=request.max_videos,
                progress_callback=progress_callback,
            )
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

        return PlaylistQuizStatusResponse(
            status="completed",
            playlist_title=result.get("playlist_title", "Unknown"),
            total_videos=result.get("total_videos", 0),
            processed=result.get("processed", 0),
            failed=result.get("failed", 0),
            drive_folder_url=result.get("drive_folder_url"),
            video_results=result.get("video_results", [])
        )
