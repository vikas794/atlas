from typing import List, Optional
from pydantic import BaseModel


class VideoQuizResult(BaseModel):
    position: int
    video_id: str
    title: str
    status: str
    doc_url: Optional[str] = None


class PlaylistQuizRequest(BaseModel):
    playlist_url: str
    gemini_api_key: Optional[str] = None
    use_env_keys: bool = True
    max_videos: Optional[int] = None


class PlaylistQuizStatusResponse(BaseModel):
    status: str
    playlist_title: str
    total_videos: int
    processed: int
    failed: int
    drive_folder_url: Optional[str] = None
    video_results: List[VideoQuizResult]


class DriveStatusResponse(BaseModel):
    configured: bool
    message: str