import os
import io
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload

SCOPES = ['https://www.googleapis.com/auth/drive.readonly']

class GoogleDriveManager:
    def __init__(self, json_key_path='service_account.json'):
        if not os.path.exists(json_key_path):
            raise FileNotFoundError(f"Critical Error: {json_key_path} not found in the project root directory.")
        
        self.creds = service_account.Credentials.from_service_account_file(
            json_key_path, scopes=SCOPES
        )
        self.service = build('drive', 'v3', credentials=self.creds)

    def list_audio_files(self, folder_id):
        query = f"'{folder_id}' in parents and (mimeType contains 'audio/' or name contains '.mp3' or name contains '.wav') and trashed = false"
        try:
            results = self.service.files().list(
                q=query,
                fields="files(id, name)",
                pageSize=100
            ).execute()
            return results.get('files', [])
        except Exception as e:
            print(f"Error fetching files from Google Drive: {e}")
            return []

    def download_file(self, file_id, dest_path):
        try:
            request = self.service.files().get_media(fileId=file_id)
            with io.FileIO(dest_path, 'wb') as fh:
                downloader = MediaIoBaseDownload(fh, request)
                done = False
                while not done:
                    status, done = downloader.next_chunk()
            return True
        except Exception as e:
            print(f"Error downloading file {file_id}: {e}")
            return False
