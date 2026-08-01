from fastapi import APIRouter, UploadFile, File, HTTPException
import shutil
from pathlib import Path

from backend.schemas.quiz import PlaylistQuizRequest, PlaylistQuizStatusResponse, DriveStatusResponse
from backend.services.quiz_service import QuizService

router = APIRouter(prefix="/api/quiz", tags=["quiz"])
quiz_service = QuizService()

@router.post("/playlist", response_model=PlaylistQuizStatusResponse)
def create_playlist_quiz(request: PlaylistQuizRequest):
    return quiz_service.process_playlist(request)

@router.get("/drive-status", response_model=DriveStatusResponse)
def get_drive_status():
    configured = quiz_service.check_drive_auth()
    return DriveStatusResponse(
        configured=configured,
        message="Google Drive credentials ready." if configured else "Missing credentials.json or token.json in root directory."
    )

@router.post("/credentials")
def upload_credentials(file: UploadFile = File(...)):
    if not file.filename.endswith(".json"):
        raise HTTPException(status_code=400, detail="Must be a JSON file")
    
    with open("credentials.json", "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)
    return {"status": "success", "message": "Credentials uploaded successfully."}

@router.post("/auth")
def authenticate_drive():
    try:
        from src.playlist_quiz_generator import GoogleDriveExporter
        GoogleDriveExporter() # Trigger the auth flow
        return {"status": "success", "message": "Authenticated successfully."}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
