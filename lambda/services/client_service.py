"""
クライアント管理サービス - JSONからクライアント情報を管理
会社フォルダの作成、メールアドレスへの共有権限付与
"""
import json
import os
from typing import Optional, List, Dict
from dataclasses import dataclass

from google.oauth2 import service_account
from googleapiclient.discovery import build


@dataclass
class Contact:
    """連絡先情報"""
    name: str
    email: str


@dataclass
class Client:
    """クライアント情報"""
    group_id: str
    company_name: str
    contacts: List[Contact]
    drive_folder_id: Optional[str]
    notes: str = ""


class ClientService:
    """クライアント管理サービス"""

    def __init__(self, clients_file: str = None, service_account_info: dict = None, root_folder_id: str = None):
        self.clients_file = clients_file or os.path.join(os.path.dirname(__file__), '..', 'clients.json')
        self.service_account_info = service_account_info or json.loads(
            os.environ.get('GOOGLE_SERVICE_ACCOUNT', '{}')
        )
        self.root_folder_id = root_folder_id or os.environ.get('GOOGLE_DRIVE_FOLDER_ID')
        self._clients_cache = None
        self._drive_service = None

    @property
    def drive_service(self):
        """Google Drive APIサービスを取得"""
        if self._drive_service is None:
            credentials = service_account.Credentials.from_service_account_info(
                self.service_account_info,
                scopes=['https://www.googleapis.com/auth/drive']
            )
            self._drive_service = build('drive', 'v3', credentials=credentials)
        return self._drive_service

    def _load_clients(self) -> Dict:
        """クライアントJSONを読み込み"""
        if self._clients_cache is not None:
            return self._clients_cache

        try:
            with open(self.clients_file, 'r', encoding='utf-8') as f:
                self._clients_cache = json.load(f)
                return self._clients_cache
        except FileNotFoundError:
            print(f"Clients file not found: {self.clients_file}")
            return {"clients": []}
        except Exception as ex:
            print(f"Error loading clients: {str(ex)}")
            return {"clients": []}

    def _save_clients(self, data: Dict):
        """クライアントJSONを保存"""
        try:
            with open(self.clients_file, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            self._clients_cache = data
        except Exception as ex:
            print(f"Error saving clients: {str(ex)}")

    def get_client_by_group_id(self, group_id: str) -> Optional[Client]:
        """グループIDからクライアントを取得"""
        data = self._load_clients()

        for client_data in data.get('clients', []):
            if client_data.get('group_id') == group_id:
                contacts = [
                    Contact(name=c.get('name', ''), email=c.get('email', ''))
                    for c in client_data.get('contacts', [])
                ]
                return Client(
                    group_id=client_data['group_id'],
                    company_name=client_data['company_name'],
                    contacts=contacts,
                    drive_folder_id=client_data.get('drive_folder_id'),
                    notes=client_data.get('notes', '')
                )
        return None

    def get_client_by_user_id(self, user_id: str) -> Optional[Client]:
        """ユーザーIDからクライアントを取得（個人チャット用）"""
        # 個人チャットの場合は user_{user_id} 形式で登録
        return self.get_client_by_group_id(f"user_{user_id}")

    def get_or_create_company_folder(self, client: Client) -> Optional[str]:
        """会社フォルダを取得または作成し、フォルダIDを返す"""
        # 既にフォルダIDがあればそれを返す
        if client.drive_folder_id:
            return client.drive_folder_id

        try:
            # 会社フォルダを作成
            folder_metadata = {
                'name': client.company_name,
                'mimeType': 'application/vnd.google-apps.folder',
                'parents': [self.root_folder_id]
            }
            folder = self.drive_service.files().create(
                body=folder_metadata,
                fields='id, webViewLink',
                supportsAllDrives=True
            ).execute()

            folder_id = folder['id']
            print(f"Created company folder: {client.company_name} -> {folder_id}")

            # 担当者にフォルダを共有
            for contact in client.contacts:
                if contact.email:
                    self._share_folder_with_email(folder_id, contact.email)

            # JSONを更新（フォルダIDを保存）
            self._update_client_folder_id(client.group_id, folder_id)

            return folder_id

        except Exception as ex:
            print(f"Error creating company folder: {str(ex)}")
            return None

    def _share_folder_with_email(self, folder_id: str, email: str):
        """フォルダをメールアドレスに共有"""
        try:
            permission = {
                'type': 'user',
                'role': 'reader',  # 閲覧権限
                'emailAddress': email
            }
            self.drive_service.permissions().create(
                fileId=folder_id,
                body=permission,
                sendNotificationEmail=False,  # 通知メールを送らない
                supportsAllDrives=True
            ).execute()
            print(f"Shared folder with: {email}")
        except Exception as ex:
            print(f"Error sharing folder with {email}: {str(ex)}")

    def _update_client_folder_id(self, group_id: str, folder_id: str):
        """クライアントのフォルダIDを更新"""
        data = self._load_clients()

        for client_data in data.get('clients', []):
            if client_data.get('group_id') == group_id:
                client_data['drive_folder_id'] = folder_id
                break

        self._save_clients(data)

    def get_company_name(self, group_id: str, user_id: str) -> str:
        """会社名を取得（クライアント未登録の場合は'未登録'）"""
        client = self.get_client_by_group_id(group_id) if group_id else self.get_client_by_user_id(user_id)

        if client:
            return client.company_name
        return "未登録クライアント"

    def is_registered_client(self, group_id: str, user_id: str) -> bool:
        """登録済みクライアントかどうか"""
        client = self.get_client_by_group_id(group_id) if group_id else self.get_client_by_user_id(user_id)
        return client is not None
