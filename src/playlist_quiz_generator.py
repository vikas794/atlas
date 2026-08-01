import os
import re
import yaml
import time
import json
import tempfile
import logging
from pathlib import Path
from typing import List, Dict, Optional, Any

from googleapiclient.discovery import build
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
import google.generativeai as genai

from src.utils import get_config, setup_logging, get_worker_count
from src.fetch_youtube_transcript import YouTubeTranscriptFetcher

# If modifying these scopes, delete the file token.json.
SCOPES = [
    'https://www.googleapis.com/auth/drive.file',
    'https://www.googleapis.com/auth/documents'
]


class PlaylistVideoFetcher:
    """Fetches video information from a YouTube playlist."""

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or os.getenv("YOUTUBE_API_KEY")
        if not self.api_key:
            raise ValueError("YOUTUBE_API_KEY is not set.")
        self.youtube = build("youtube", "v3", developerKey=self.api_key)
        self.logger = logging.getLogger(__name__)

    def fetch_playlist_videos(self, playlist_id: str, max_videos: Optional[int] = None) -> List[Dict[str, Any]]:
        """Fetches videos from a playlist. Returns list of dicts with video details."""
        videos = []
        next_page_token = None

        while True:
            request = self.youtube.playlistItems().list(
                part="snippet,contentDetails",
                playlistId=playlist_id,
                maxResults=50,
                pageToken=next_page_token
            )
            response = request.execute()

            for item in response.get("items", []):
                snippet = item.get("snippet", {})
                video_title = snippet.get("title", "")
                if video_title in ["Private video", "Deleted video"]:
                    continue
                
                video_id = snippet.get("resourceId", {}).get("videoId")
                if video_id:
                    videos.append({
                        "video_id": video_id,
                        "title": video_title,
                        "position": snippet.get("position", 0),
                        "channel": snippet.get("videoOwnerChannelTitle", ""),
                    })

                if max_videos and len(videos) >= max_videos:
                    break

            next_page_token = response.get("nextPageToken")
            if not next_page_token or (max_videos and len(videos) >= max_videos):
                break

        return videos[:max_videos] if max_videos else videos

    def get_playlist_title(self, playlist_id: str) -> str:
        request = self.youtube.playlists().list(
            part="snippet",
            id=playlist_id
        )
        response = request.execute()
        items = response.get("items", [])
        if items:
            return items[0]["snippet"]["title"]
        return "Untitled Playlist"


class QuizGenerator:
    """Generates active-recall quizzes from YouTube video transcripts using Gemini."""

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or os.getenv("GEMINI_API_KEY")
        if not self.api_key:
            raise ValueError("GEMINI_API_KEY is not set.")
        
        genai.configure(api_key=self.api_key)
        self.model_name = get_config("api.gemini.model", "gemini-3-flash-preview")
        self.model = genai.GenerativeModel(self.model_name)
        
        self.prompt_template = self._load_prompt_template()
        self.logger = logging.getLogger(__name__)
        
        # Retry settings from config
        self.max_retries = get_config("playlist_quiz.max_retries", 3)

    def _load_prompt_template(self) -> str:
        current_dir = Path(__file__).parent.parent
        prompt_path = current_dir / "src" / "prompts" / "quiz_generator.yaml"
        with open(prompt_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
            return data["prompt"]

    def _read_srt(self, srt_path: str) -> str:
        with open(srt_path, "r", encoding="utf-8") as f:
            content = f.read()
            
        lines = content.split("\n")
        text_lines = [line.strip() for line in lines if line.strip() and not line.isdigit() and "-->" not in line]
        return " ".join(text_lines)

    def generate_quiz(self, video_id: str, title: str, transcript_folder: str) -> Optional[str]:
        # Assume transcript was fetched and exists in the designated folder
        expected_srt = os.path.join(transcript_folder, f"{video_id}.srt")
        expected_srt_en = os.path.join(transcript_folder, f"{video_id}.en.srt")
        
        srt_path = expected_srt_en if os.path.exists(expected_srt_en) else expected_srt
        if not os.path.exists(srt_path):
            self.logger.warning(f"Transcript not found for {video_id} at {srt_path}")
            return None

        transcript_text = self._read_srt(srt_path)
        if not transcript_text.strip():
            self.logger.warning(f"Transcript empty for {video_id}")
            return None

        prompt = f"{self.prompt_template}\n\n**Video Title**: {title}\n**Transcript**:\n{transcript_text}"
        
        # Retry logic with exponential backoff
        for attempt in range(self.max_retries):
            try:
                response = self.model.generate_content(prompt)
                return response.text
            except Exception as e:
                wait_time = 2 ** attempt
                self.logger.warning(f"Error generating quiz for {video_id} (Attempt {attempt+1}/{self.max_retries}): {e}. Retrying in {wait_time}s...")
                if attempt < self.max_retries - 1:
                    time.sleep(wait_time)
                else:
                    self.logger.error(f"Failed to generate quiz for {video_id} after {self.max_retries} attempts.")
                    return None
        return None

class GoogleDriveExporter:
    """Exports content as Google Docs in Google Drive."""

    def __init__(self):
        self.logger = logging.getLogger(__name__)
        self.creds = self._get_credentials()
        self.drive_service = build('drive', 'v3', credentials=self.creds)
        self.docs_service = build('docs', 'v1', credentials=self.creds)

    def _get_credentials(self) -> Credentials:
        creds = None
        if os.path.exists('token.json'):
            creds = Credentials.from_authorized_user_file('token.json', SCOPES)
            
        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            else:
                if not os.path.exists('credentials.json'):
                    raise FileNotFoundError("Google Drive OAuth requires 'credentials.json' in the root directory.")
                
                # Handle 'web' vs 'installed' client types by rewriting in-memory to a temp file if needed
                with open('credentials.json', 'r') as f:
                    creds_data = json.load(f)
                
                if 'web' in creds_data:
                    self.logger.info("Detected 'web' credentials. Generating compatible 'installed' flow...")
                    creds_data['installed'] = creds_data.pop('web')
                    # InstalledAppFlow requires redirect_uris
                    if 'redirect_uris' not in creds_data['installed']:
                        creds_data['installed']['redirect_uris'] = ['http://localhost:8080/']
                    else:
                        if 'http://localhost:8080/' not in creds_data['installed']['redirect_uris']:
                            creds_data['installed']['redirect_uris'].append('http://localhost:8080/')
                    
                    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as temp_creds:
                        json.dump(creds_data, temp_creds)
                        temp_creds_path = temp_creds.name
                    
                    try:
                        flow = InstalledAppFlow.from_client_secrets_file(temp_creds_path, SCOPES)
                        creds = flow.run_local_server(port=8080)
                    finally:
                        os.unlink(temp_creds_path)
                else:
                    flow = InstalledAppFlow.from_client_secrets_file('credentials.json', SCOPES)
                    creds = flow.run_local_server(port=8080)
                    
            with open('token.json', 'w') as token:
                token.write(creds.to_json())
        return creds

    def create_folder(self, folder_name: str) -> str:
        file_metadata = {
            'name': folder_name,
            'mimeType': 'application/vnd.google-apps.folder'
        }
        file = self.drive_service.files().create(body=file_metadata, fields='id').execute()
        return file.get('id')

    def create_doc_in_folder(self, title: str, content: str, folder_id: str) -> str:
        # Create empty doc in folder
        file_metadata = {
            'name': title,
            'mimeType': 'application/vnd.google-apps.document',
            'parents': [folder_id]
        }
        doc = self.drive_service.files().create(body=file_metadata, fields='id').execute()
        doc_id = doc.get('id')
        
        # Write content to doc
        requests = [
            {
                'insertText': {
                    'location': {
                        'index': 1,
                    },
                    'text': content
                }
            }
        ]
        self.docs_service.documents().batchUpdate(
            documentId=doc_id, body={'requests': requests}).execute()
            
        return doc_id


class PlaylistQuizPipeline:
    """Orchestrates fetching playlist, transcripts, generating quizzes, and exporting."""

    def __init__(self, youtube_api_key: Optional[str] = None, gemini_api_key: Optional[str] = None):
        self.fetcher = PlaylistVideoFetcher(api_key=youtube_api_key)
        self.quiz_gen = QuizGenerator(api_key=gemini_api_key)
        self.exporter = None # Lazy load to prevent immediate auth blocking
        self.logger = logging.getLogger(__name__)
        self.delay_between_requests = get_config("playlist_quiz.delay_between_requests", 2)

    def extract_playlist_id(self, url: str) -> str:
        # e.g. https://www.youtube.com/playlist?list=PLxxxxx
        match = re.search(r"[?&]list=([^#&?]+)", url)
        if match:
            return match.group(1)
        return url

    def run(self, playlist_url: str, max_videos: Optional[int] = None) -> Dict[str, Any]:
        setup_logging()
        
        # Verify Drive auth upfront
        if not self.exporter:
            self.exporter = GoogleDriveExporter()

        playlist_id = self.extract_playlist_id(playlist_url)
        playlist_title = self.fetcher.get_playlist_title(playlist_id)
        self.logger.info(f"Found playlist: {playlist_title}")

        videos = self.fetcher.fetch_playlist_videos(playlist_id, max_videos=max_videos)
        self.logger.info(f"Found {len(videos)} videos in playlist.")

        # Folder creation
        folder_id = self.exporter.create_folder(playlist_title)
        folder_url = f"https://drive.google.com/drive/folders/{folder_id}"
        self.logger.info(f"Created Google Drive folder: {folder_url}")

        # Fetch Transcripts (reuse existing fetcher)
        transcript_folder = get_config("playlist_quiz.output_folder", "quiz_output")
        os.makedirs(transcript_folder, exist_ok=True)
        
        transcript_fetcher = YouTubeTranscriptFetcher(output_folder=transcript_folder)
        urls = [f"https://www.youtube.com/watch?v={v['video_id']}" for v in videos]
        transcript_fetcher.fetch_transcripts(urls)

        results = []
        for i, v in enumerate(videos):
            pos = v["position"] + 1
            title_prefix = f"{pos}. {v['title']}"
            self.logger.info(f"Generating quiz for: {title_prefix}")
            
            quiz_content = self.quiz_gen.generate_quiz(v["video_id"], v["title"], transcript_folder)
            if quiz_content:
                try:
                    doc_id = self.exporter.create_doc_in_folder(title=title_prefix, content=quiz_content, folder_id=folder_id)
                    doc_url = f"https://docs.google.com/document/d/{doc_id}/edit"
                    status = "Generated & Uploaded"
                except Exception as e:
                    self.logger.error(f"Failed to upload doc for {v['video_id']}: {e}")
                    doc_url = None
                    status = "Failed Upload"
            else:
                doc_url = None
                status = "Failed Generation (No Transcript or API Error)"
                
            results.append({
                "position": pos,
                "video_id": v["video_id"],
                "title": v["title"],
                "status": status,
                "doc_url": doc_url
            })
            
            # Rate limiting delay (skip on the last item)
            if i < len(videos) - 1:
               self.logger.info(f"Waiting {self.delay_between_requests}s to avoid rate limits...")
               time.sleep(self.delay_between_requests)
            
        return {
            "playlist_title": playlist_title,
            "total_videos": len(videos),
            "processed": sum(1 for r in results if r["doc_url"] is not None),
            "failed": sum(1 for r in results if r["doc_url"] is None),
            "drive_folder_url": folder_url,
            "video_results": results
        }
