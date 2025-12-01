"""
ダウンロードサービス - 各種URLからファイルをダウンロード
対応: Google Drive, Dropbox, ギガファイル便, 一般URL
"""
import re
import urllib.request
import urllib.parse
import json
import io
import os
from typing import Optional, Tuple, List
from dataclasses import dataclass

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload


@dataclass
class DownloadedFile:
    """ダウンロードしたファイル情報"""
    filename: str
    content: bytes
    content_type: str
    source_url: str


class DownloadService:
    """各種URLからファイルをダウンロードするサービス"""

    def __init__(self, service_account_info: dict = None):
        self.service_account_info = service_account_info or json.loads(
            os.environ.get('GOOGLE_SERVICE_ACCOUNT', '{}')
        )

    def download_from_url(self, url: str) -> Optional[DownloadedFile]:
        """URLからファイルをダウンロード（URL種別を自動判定）"""
        url_type = self._detect_url_type(url)

        if url_type == 'google_drive':
            return self._download_from_google_drive(url)
        elif url_type == 'dropbox':
            return self._download_from_dropbox(url)
        elif url_type == 'gigafile':
            return self._download_from_gigafile(url)
        else:
            return self._download_from_general_url(url)

    def download_all(self, urls: List[str]) -> List[DownloadedFile]:
        """複数URLからファイルをダウンロード"""
        files = []
        for url in urls:
            try:
                file = self.download_from_url(url)
                if file:
                    files.append(file)
            except Exception as ex:
                print(f"Download Error ({url}): {str(ex)}")
        return files

    def _detect_url_type(self, url: str) -> str:
        """URL種別を判定"""
        if 'drive.google.com' in url or 'docs.google.com' in url:
            return 'google_drive'
        elif 'dropbox.com' in url or 'dl.dropboxusercontent.com' in url:
            return 'dropbox'
        elif 'gigafile.nu' in url:
            return 'gigafile'
        else:
            return 'general'

    def _download_from_google_drive(self, url: str) -> Optional[DownloadedFile]:
        """Google Driveからダウンロード"""
        try:
            # ファイルIDを抽出
            file_id = self._extract_google_drive_file_id(url)
            if not file_id:
                print(f"Could not extract file ID from: {url}")
                return None

            # Google Drive APIでダウンロード
            credentials = service_account.Credentials.from_service_account_info(
                self.service_account_info,
                scopes=['https://www.googleapis.com/auth/drive.readonly']
            )
            service = build('drive', 'v3', credentials=credentials)

            # ファイルメタデータを取得
            file_metadata = service.files().get(
                fileId=file_id,
                fields='name, mimeType',
                supportsAllDrives=True
            ).execute()

            # ファイルをダウンロード
            request = service.files().get_media(fileId=file_id)
            content = request.execute()

            return DownloadedFile(
                filename=file_metadata['name'],
                content=content,
                content_type=file_metadata.get('mimeType', 'application/octet-stream'),
                source_url=url
            )

        except Exception as ex:
            print(f"Google Drive Download Error: {str(ex)}")
            return None

    def _extract_google_drive_file_id(self, url: str) -> Optional[str]:
        """Google DriveのURLからファイルIDを抽出"""
        patterns = [
            r'/file/d/([a-zA-Z0-9_-]+)',
            r'/open\?id=([a-zA-Z0-9_-]+)',
            r'id=([a-zA-Z0-9_-]+)',
            r'/folders/([a-zA-Z0-9_-]+)',
        ]
        for pattern in patterns:
            match = re.search(pattern, url)
            if match:
                return match.group(1)
        return None

    def _download_from_dropbox(self, url: str) -> Optional[DownloadedFile]:
        """Dropboxからダウンロード"""
        try:
            # Dropboxの共有リンクを直接ダウンロードリンクに変換
            download_url = url.replace('www.dropbox.com', 'dl.dropboxusercontent.com')
            download_url = download_url.replace('?dl=0', '?dl=1')
            if '?dl=1' not in download_url:
                download_url += '?dl=1' if '?' not in download_url else '&dl=1'

            req = urllib.request.Request(download_url)
            req.add_header('User-Agent', 'Mozilla/5.0')

            with urllib.request.urlopen(req, timeout=60) as response:
                content = response.read()
                content_type = response.headers.get('Content-Type', 'application/octet-stream')

                # ファイル名を抽出
                content_disposition = response.headers.get('Content-Disposition', '')
                filename = self._extract_filename_from_header(content_disposition)
                if not filename:
                    filename = url.split('/')[-1].split('?')[0] or 'dropbox_file'

                return DownloadedFile(
                    filename=filename,
                    content=content,
                    content_type=content_type,
                    source_url=url
                )

        except Exception as ex:
            print(f"Dropbox Download Error: {str(ex)}")
            return None

    def _download_from_gigafile(self, url: str) -> Optional[DownloadedFile]:
        """ギガファイル便からダウンロード"""
        try:
            # ギガファイル便のページからダウンロードリンクを取得
            req = urllib.request.Request(url)
            req.add_header('User-Agent', 'Mozilla/5.0')

            with urllib.request.urlopen(req, timeout=30) as response:
                html = response.read().decode('utf-8', errors='ignore')

            # ダウンロードリンクを抽出
            download_url = self._extract_gigafile_download_url(html)
            if not download_url:
                print("Could not find download link in gigafile page")
                return None

            # ファイルをダウンロード
            req = urllib.request.Request(download_url)
            req.add_header('User-Agent', 'Mozilla/5.0')
            req.add_header('Referer', url)

            with urllib.request.urlopen(req, timeout=120) as response:
                content = response.read()
                content_type = response.headers.get('Content-Type', 'application/octet-stream')

                content_disposition = response.headers.get('Content-Disposition', '')
                filename = self._extract_filename_from_header(content_disposition)
                if not filename:
                    filename = 'gigafile_download'

                return DownloadedFile(
                    filename=filename,
                    content=content,
                    content_type=content_type,
                    source_url=url
                )

        except Exception as ex:
            print(f"Gigafile Download Error: {str(ex)}")
            return None

    def _extract_gigafile_download_url(self, html: str) -> Optional[str]:
        """ギガファイル便のHTMLからダウンロードURLを抽出"""
        patterns = [
            r'id="download_list"[^>]*data-url="([^"]+)"',
            r'download_url\s*=\s*["\']([^"\']+)["\']',
            r'href="(https?://[^"]*gigafile[^"]*download[^"]*)"',
        ]
        for pattern in patterns:
            match = re.search(pattern, html)
            if match:
                return match.group(1)
        return None

    def _download_from_general_url(self, url: str) -> Optional[DownloadedFile]:
        """一般的なURLからダウンロード"""
        try:
            req = urllib.request.Request(url)
            req.add_header('User-Agent', 'Mozilla/5.0')

            with urllib.request.urlopen(req, timeout=60) as response:
                content = response.read()
                content_type = response.headers.get('Content-Type', 'application/octet-stream')

                content_disposition = response.headers.get('Content-Disposition', '')
                filename = self._extract_filename_from_header(content_disposition)
                if not filename:
                    filename = url.split('/')[-1].split('?')[0] or 'downloaded_file'

                return DownloadedFile(
                    filename=filename,
                    content=content,
                    content_type=content_type,
                    source_url=url
                )

        except Exception as ex:
            print(f"General URL Download Error: {str(ex)}")
            return None

    def _extract_filename_from_header(self, content_disposition: str) -> Optional[str]:
        """Content-Dispositionヘッダーからファイル名を抽出"""
        if not content_disposition:
            return None

        # filename*= (RFC 5987) を優先
        match = re.search(r"filename\*=(?:UTF-8''|utf-8'')([^;]+)", content_disposition)
        if match:
            return urllib.parse.unquote(match.group(1))

        # filename= を試す
        match = re.search(r'filename="([^"]+)"', content_disposition)
        if match:
            return match.group(1)

        match = re.search(r'filename=([^;\s]+)', content_disposition)
        if match:
            return match.group(1)

        return None


class FileUploader:
    """ダウンロードしたファイルをGoogle Driveにアップロード"""

    def __init__(self, service_account_info: dict = None):
        self.service_account_info = service_account_info or json.loads(
            os.environ.get('GOOGLE_SERVICE_ACCOUNT', '{}')
        )
        self._service = None

    @property
    def service(self):
        if self._service is None:
            credentials = service_account.Credentials.from_service_account_info(
                self.service_account_info,
                scopes=['https://www.googleapis.com/auth/drive']
            )
            self._service = build('drive', 'v3', credentials=credentials)
        return self._service

    def upload_to_folder(self, file: DownloadedFile, folder_id: str) -> Optional[str]:
        """ファイルをGoogle Driveフォルダにアップロード"""
        try:
            file_metadata = {
                'name': file.filename,
                'parents': [folder_id]
            }

            media = MediaIoBaseUpload(
                io.BytesIO(file.content),
                mimetype=file.content_type
            )

            uploaded = self.service.files().create(
                body=file_metadata,
                media_body=media,
                fields='id, webViewLink',
                supportsAllDrives=True
            ).execute()

            print(f"Uploaded: {file.filename} -> {uploaded.get('webViewLink')}")
            return uploaded.get('webViewLink')

        except Exception as ex:
            print(f"Upload Error ({file.filename}): {str(ex)}")
            return None

    def upload_files_to_folder(self, files: List[DownloadedFile], folder_id: str) -> List[str]:
        """複数ファイルをアップロード"""
        links = []
        for file in files:
            link = self.upload_to_folder(file, folder_id)
            if link:
                links.append(link)
        return links
