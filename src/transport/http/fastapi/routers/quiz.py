from __future__ import annotations

import json
from queue import Queue
from threading import Thread

from fastapi import APIRouter, Depends, UploadFile, File, HTTPException
from fastapi.responses import StreamingResponse
import shutil
from pathlib import Path

from src.application.dto.quiz import QuizGenerationInput, QuizGenerationOutput, VideoQuizResult
from src.application.use_cases import GenerateQuizUseCase
from src.transport.http.fastapi.dependencies import get_quiz_use_case
from src.transport.http.fastapi.schemas.quiz import PlaylistQuizRequest, PlaylistQuizStatusResponse, DriveStatusResponse

router = APIRouter(prefix="/api/quiz", tags=["quiz"])


def _to_quiz_input(request: PlaylistQuizRequest) -> QuizGenerationInput:
    return QuizGenerationInput(
        playlist_url=request.playlist_url,
        gemini_api_key=request.gemini_api_key,
        use_env_keys=request.use_env_keys,
        max_videos=request.max_videos,
        progress_callback=None,  # Will be set in streaming endpoint
    )


def _to_status_response(output: QuizGenerationOutput) -> PlaylistQuizStatusResponse:
    return PlaylistQuizStatusResponse(
        status=output.status,
        playlist_title=output.playlist_title,
        total_videos=output.total_videos,
        processed=output.processed,
        failed=output.failed,
        drive_folder_url=output.drive_folder_url,
        video_results=[
            {
                "position": r.position,
                "video_id": r.video_id,
                "title": r.title,
                "status": r.status,
                "doc_url": r.doc_url,
            }
            for r in output.video_results
        ],
    )


@router.post("/playlist", response_model=PlaylistQuizStatusResponse)
async def create_playlist_quiz(
    request: PlaylistQuizRequest,
    use_case: GenerateQuizUseCase = Depends(get_quiz_use_case),
) -> PlaylistQuizStatusResponse:
    input_dto = _to_quiz_input(request)
    output = await use_case.execute(input_dto)
    return _to_status_response(output)


@router.post("/playlist/stream")
async def create_playlist_quiz_stream(
    request: PlaylistQuizRequest,
    use_case: GenerateQuizUseCase = Depends(get_quiz_use_case),
):
    """Run the quiz pipeline while forwarding real pipeline milestones as SSE events."""
    events: Queue = Queue()

    def publish(progress: dict) -> None:
        events.put(("progress", progress))

    def run_pipeline():
        try:
            input_dto = QuizGenerationInput(
                playlist_url=request.playlist_url,
                gemini_api_key=request.gemini_api_key,
                use_env_keys=request.use_env_keys,
                max_videos=request.max_videos,
                progress_callback=publish,
            )
            result = use_case.execute(input_dto)
            result_payload = _to_status_response(result)
            events.put(("complete", result_payload.model_dump() if hasattr(result_payload, "model_dump") else result_payload.dict()))
        except HTTPException as error:
            events.put(("error", {"message": str(error.detail)}))
        except Exception as error:
            events.put(("error", {"message": str(error)}))

    Thread(target=run_pipeline, daemon=True).start()
    while True:
        event_type, payload = events.get()
        yield f"data: {json.dumps({'type': event_type, **payload})}\n\n"
        if event_type in {"complete", "error"}:
            return


@router.get("/drive-status", response_model=DriveStatusResponse)
async def get_drive_status(
    use_case: GenerateQuizUseCase = Depends(get_quiz_use_case),
) -> DriveStatusResponse:
    # Check if Drive credentials/tokens are in place
    import os
    has_token = os.path.exists('token.json')
    has_creds = os.path.exists('credentials.json')
    configured = has_token or has_creds
    return DriveStatusResponse(
        configured=configured,
        message="Google Drive credentials ready." if configured else "Missing credentials.json or token.json in root directory.",
    )


@router.post("/credentials")
async def upload_credentials(file: UploadFile = File(...)):
    if not file.filename or not file.filename.endswith(".json"):
        raise HTTPException(status_code=400, detail="Must be a JSON file")

    with open("credentials.json", "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)
    return {"status": "success", "message": "Credentials uploaded successfully."}


@router.post("/auth")
async def authenticate_drive():
    try:
        from src.infrastructure.google.drive import GoogleDriveExporter
        GoogleDriveExporter()  # Trigger the auth flow
        return {"status": "success", "message": "Authenticated successfully."}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))