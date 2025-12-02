"""
Google Drive サービス
"""
import json
import io
import os
from datetime import datetime
from typing import List, Optional, Tuple

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload


class DriveService:
    """Google Drive操作を行うサービス"""

    SCOPES = ['https://www.googleapis.com/auth/drive']

    def __init__(self, service_account_info: dict = None, folder_id: str = None):
        self.service_account_info = service_account_info or json.loads(
            os.environ.get('GOOGLE_SERVICE_ACCOUNT', '{}')
        )
        self.folder_id = folder_id or os.environ.get('GOOGLE_DRIVE_FOLDER_ID')
        self._service = None

    @property
    def service(self):
        """Google Drive APIサービスを取得（遅延初期化）"""
        if self._service is None:
            credentials = service_account.Credentials.from_service_account_info(
                self.service_account_info,
                scopes=self.SCOPES
            )
            self._service = build('drive', 'v3', credentials=credentials)
        return self._service

    def create_order_folder(
        self,
        order_id: str,
        customer_name: str,
        urls: List[str],
        parent_folder_id: Optional[str] = None,
        project_name: Optional[str] = None
    ) -> Optional[Tuple[str, str]]:
        """依頼用フォルダを作成

        Args:
            order_id: 依頼ID
            customer_name: 顧客名
            urls: URL一覧
            parent_folder_id: 親フォルダID（会社フォルダ）。なければルートフォルダ
            project_name: 案件名

        Returns:
            Tuple[folder_url, folder_id] or None
        """
        try:
            today = datetime.now().strftime('%Y-%m-%d')
            # 担当者名から「様」を除いた名前を取得
            staff_name = customer_name.split(' - ')[0] if ' - ' in customer_name else customer_name
            staff_name = staff_name.replace('様', '').strip()

            # フォルダ名を構築: 日付_案件名_担当者様_ID
            if project_name:
                folder_name = f"{today}_{project_name}_{staff_name}様_{order_id[:8]}"
            else:
                folder_name = f"{today}_{staff_name}様_{order_id[:8]}"

            # 親フォルダを決定（会社フォルダがあればそこ、なければルート）
            target_parent = parent_folder_id if parent_folder_id else self.folder_id

            # フォルダ作成
            folder_metadata = {
                'name': folder_name,
                'mimeType': 'application/vnd.google-apps.folder',
                'parents': [target_parent]
            }
            folder = self.service.files().create(
                body=folder_metadata,
                fields='id, webViewLink',
                supportsAllDrives=True
            ).execute()

            # URL一覧ファイルを作成
            if urls:
                url_content = "依頼元URL一覧:\n\n" + "\n".join(urls)
                file_metadata = {
                    'name': 'source_urls.txt',
                    'parents': [folder['id']]
                }
                media = MediaIoBaseUpload(
                    io.BytesIO(url_content.encode('utf-8')),
                    mimetype='text/plain'
                )
                self.service.files().create(
                    body=file_metadata,
                    media_body=media,
                    supportsAllDrives=True
                ).execute()

            print(f"Folder created: {folder['webViewLink']}")
            return folder['webViewLink'], folder['id']

        except Exception as ex:
            print(f"Drive Error: {str(ex)}")
            return None

    def create_order_folder_legacy(self, order_id: str, customer_name: str, urls: List[str]) -> Optional[str]:
        """依頼用フォルダを作成（後方互換用）"""
        result = self.create_order_folder(order_id, customer_name, urls)
        if result:
            return result[0]  # URLのみ返す
        return None
