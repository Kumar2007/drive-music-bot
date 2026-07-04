import io
from googleapiclient.http import MediaIoBaseDownload
import os
import json
from google.oauth2 import service_account
from googleapiclient.discovery import build

class GoogleDriveManager:
    def __init__(self):
        # Define scopes
        scopes = ['https://www.googleapis.com/auth/drive.readonly']
        
        # 1. Check if we are running in the cloud (Render) using the environment variable
        env_creds = os.getenv('GOOGLE_CREDS_JSON')
        
        if env_creds:
            try:
                # Parse the raw string variable directly into a python dictionary
                creds_dict = json.loads(env_creds)
                self.creds = service_account.Credentials.from_service_account_info(
                    creds_dict, scopes=scopes
                )
                print("Authenticated successfully using Render Environment Variables.")
            except Exception as e:
                raise RuntimeError(f"Failed to parse GOOGLE_CREDS_JSON environment variable: {e}")
        
        # 2. Fall back to local file if running on your laptop
        elif os.path.exists('service_account.json'):
            self.creds = service_account.Credentials.from_service_account_file(
                'service_account.json', scopes=scopes
            )
            print("Authenticated successfully using local service_account.json file.")
            
        else:
            raise FileNotFoundError("Critical Error: Neither GOOGLE_CREDS_JSON env variable nor service_account.json file was found.")
            
        self.service = build('drive', 'v3', credentials=self.creds)

    def list_audio_files(self, folder_id):
        try:
            # Broadened query: Looks for anything matching general audio types or common extensions
            query = (
                f"'{folder_id}' in parents and "
                f"(mimeType contains 'audio/' or name contains '.mp3' or name contains '.m4a' or name contains '.wav') "
                f"and trashed = false"
            )
            
            results = self.service.files().list(
                q=query,
                spaces='drive',
                fields="files(id, name, mimeType)",
                pageSize=50
            ).execute()
            
            # ADD THIS DEBUG LINE HERE:
            print(f"DEBUG: Raw Google Drive API response: {results}")
            
            return results.get('files', [])
        except Exception as e:
            print(f"Error listing files from Drive: {e}")
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
