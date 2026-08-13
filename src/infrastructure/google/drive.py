from __future__ import annotations

import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Any

from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials

SCOPES = [
    "https://www.googleapis.com/auth/drive.file",
    "https://www.googleapis.com/auth/documents",
]


class GoogleDriveExporter:
    """Exports content as Google Docs in Google Drive.

    Preserves exact OAuth flow and behavior from legacy GoogleDriveExporter.
    """

    def __init__(
        self,
        creds_path: str | Path | None = None,
        token_path: str | Path | None = None,
    ) -> None:
        self.logger = logging.getLogger(__name__)

        if creds_path is None:
            creds_path = os.getenv("ATLAS_GOOGLE_CREDS_PATH", "credentials.json")
        if token_path is None:
            token_path = os.getenv("ATLAS_GOOGLE_TOKEN_PATH", "token.json")

        self.creds_path = Path(creds_path)
        self.token_path = Path(token_path)
        self.creds = self._get_credentials()
        self.drive_service = build("drive", "v3", credentials=self.creds)
        self.docs_service = build("docs", "v1", credentials=self.creds)

    def _get_credentials(self) -> Credentials:
        creds: Credentials | None = None

        if self.token_path.exists():
            creds = Credentials.from_authorized_user_file(str(self.token_path), SCOPES)

        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            else:
                if not self.creds_path.exists():
                    raise FileNotFoundError(
                        f"Google Drive OAuth requires '{self.creds_path}' in the root directory."
                    )

                with open(self.creds_path) as f:
                    creds_data = json.load(f)

                if "web" in creds_data:
                    self.logger.info(
                        "Detected 'web' credentials. Generating compatible 'installed' flow..."
                    )
                    creds_data["installed"] = creds_data.pop("web")
                    if "redirect_uris" not in creds_data["installed"]:
                        creds_data["installed"]["redirect_uris"] = ["http://localhost:8080/"]
                    else:
                        if "http://localhost:8080/" not in creds_data["installed"]["redirect_uris"]:
                            creds_data["installed"]["redirect_uris"].append("http://localhost:8080/")

                    with tempfile.NamedTemporaryFile(
                        mode="w", suffix=".json", delete=False
                    ) as temp_creds:
                        json.dump(creds_data, temp_creds)
                        temp_creds_path = temp_creds.name

                    try:
                        flow = InstalledAppFlow.from_client_secrets_file(temp_creds_path, SCOPES)
                        creds = flow.run_local_server(port=8080)
                    finally:
                        os.unlink(temp_creds_path)
                else:
                    flow = InstalledAppFlow.from_client_secrets_file(
                        str(self.creds_path), SCOPES
                    )
                    creds = flow.run_local_server(port=8080)

                with open(self.token_path, "w") as token:
                    token.write(creds.to_json())

        return creds

    def create_folder(self, folder_name: str) -> str:
        """Create a folder in Google Drive and return its ID."""
        file_metadata = {
            "name": folder_name,
            "mimeType": "application/vnd.google-apps.folder",
        }
        file = self.drive_service.files().create(body=file_metadata, fields="id").execute()
        return file.get("id")

    def create_doc_in_folder(
        self, title: str, content: str, folder_id: str | None
    ) -> str:
        """Create a Google Doc in the specified folder (or root if None) and return its ID."""
        file_metadata: dict[str, Any] = {
            "name": title,
            "mimeType": "application/vnd.google-apps.document",
        }
        if folder_id:
            file_metadata["parents"] = [folder_id]

        doc = self.drive_service.files().create(body=file_metadata, fields="id").execute()
        doc_id = doc.get("id")

        requests = [
            {
                "insertText": {
                    "location": {"index": 1},
                    "text": content,
                }
            }
        ]
        self.docs_service.documents().batchUpdate(
            documentId=doc_id, body={"requests": requests}
        ).execute()

        return doc_id
