import io
import os
import json
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload

class GoogleDriveManager:
    def __init__(self):
        # Define scopes
        scopes = ['https://www.googleapis.com/auth/drive.readonly']
        
        # Cache configuration parameters
        self.cache_dir = "temp_cache"
        self.max_cache_size = 100 * 1024 * 1024  # Strict 100 MB Limit
        
        # Ensure cache directory exists on initialization
        os.makedirs(self.cache_dir, exist_ok=True)
        
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
            
            print(f"DEBUG: Raw Google Drive API response: {results}")
            return results.get('files', [])
        except Exception as e:
            print(f"Error listing files from Drive: {e}")
            return []

    def _manage_cache_size(self, new_file_size):
        """Internal helper to ensure total directory contents stay below 100MB."""
        if not os.path.exists(self.cache_dir):
            return

        # Explicit loop to calculate size, avoiding syntax complexities
        total_size = 0
        for f in os.listdir(self.cache_dir):
            file_path = os.path.join(self.cache_dir, f)
            if os.path.isfile(file_path):
                total_size += os.path.getsize(file_path)
        
        # Evict files using an LRU (Least Recently Used) strategy if storage budget is blown
        while total_size + new_file_size > self.max_cache_size:
            files = []
            for f in os.listdir(self.cache_dir):
                file_path = os.path.join(self.cache_dir, f)
                if os.path.isfile(file_path):
                    files.append(file_path)
                    
            if not files:
                break
                
            # Evict the file with the oldest access time timestamp
            oldest_file = min(files, key=os.path.getatime)
            total_size -= os.path.getsize(oldest_file)
            try:
                os.remove(oldest_file)
                print(f"🧹 Cache Eviction: Removed stale file from storage: {oldest_file}")
            except Exception as e:
                print(f"Failed to evict cached file {oldest_file}: {e}")
                break

    def get_or_download_track(self, file_id, file_name):
        """Checks local disk storage cache first; downloads from Google Drive on cache miss."""
        local_path = os.path.join(self.cache_dir, f"{file_id}.mp3")

        # Scenario A: Cache Hit
        if os.path.exists(local_path):
            print(f"⚡ Cache Hit! Playing straight from local cache: {file_name}")
            try:
                os.utime(local_path, None)  # Refresh last access time (touches file metadata)
            except Exception:
                pass
            return local_path, True

        # Scenario B: Cache Miss (Must fetch from cloud infrastructure)
        print(f"📥 Cache Miss. Pulling from Google Drive infrastructure: {file_name}")
        try:
            request = self.service.files().get_media(fileId=file_id)
            memory_buffer = io.BytesIO()
            downloader = MediaIoBaseDownload(memory_buffer, request)
            
            done = False
            while not done:
                status, done = downloader.next_chunk()
            
            # Extract raw bytes, evaluate size restrictions, and manage space
            file_bytes = memory_buffer.getvalue()
            self._manage_cache_size(len(file_bytes))

            # Commit data to local cache block
            with open(local_path, "wb") as f:
                f.write(file_bytes)
                
            return local_path, True
        except Exception as e:
            print(f"Error resolving download pipeline for file {file_id}: {e}")
            return None, False
