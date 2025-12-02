"""
クライアント管理サービス - DynamoDBでクライアント情報を管理
会社フォルダの作成、メールアドレスへの共有権限付与
"""
import json
import os
from typing import Optional, List
from dataclasses import dataclass

import boto3
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

    def __init__(self, table_name: str = None, service_account_info: dict = None, root_folder_id: str = None):
        self.dynamodb = boto3.resource('dynamodb')
        self.table = self.dynamodb.Table(table_name or 'ai_secretary_clients')
        self.service_account_info = service_account_info or json.loads(
            os.environ.get('GOOGLE_SERVICE_ACCOUNT', '{}')
        )
        self.root_folder_id = root_folder_id or os.environ.get('GOOGLE_DRIVE_FOLDER_ID')
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

    def get_client_by_group_id(self, group_id: str) -> Optional[Client]:
        """グループIDからクライアントを取得"""
        try:
            response = self.table.get_item(Key={'group_id': group_id})
            item = response.get('Item')

            if not item:
                return None

            contacts = [
                Contact(name=c.get('name', ''), email=c.get('email', ''))
                for c in item.get('contacts', [])
            ]

            return Client(
                group_id=item['group_id'],
                company_name=item['company_name'],
                contacts=contacts,
                drive_folder_id=item.get('drive_folder_id'),
                notes=item.get('notes', '')
            )

        except Exception as ex:
            print(f"Get client error: {str(ex)}")
            return None

    def get_client_by_user_id(self, user_id: str) -> Optional[Client]:
        """ユーザーIDからクライアントを取得（個人チャット用）"""
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

            # DynamoDBを更新（フォルダIDを保存）
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
                'role': 'reader',
                'emailAddress': email
            }
            self.drive_service.permissions().create(
                fileId=folder_id,
                body=permission,
                sendNotificationEmail=False,
                supportsAllDrives=True
            ).execute()
            print(f"Shared folder with: {email}")
        except Exception as ex:
            print(f"Error sharing folder with {email}: {str(ex)}")

    def _update_client_folder_id(self, group_id: str, folder_id: str):
        """クライアントのフォルダIDをDynamoDBに更新"""
        try:
            self.table.update_item(
                Key={'group_id': group_id},
                UpdateExpression='SET drive_folder_id = :folder_id',
                ExpressionAttributeValues={':folder_id': folder_id}
            )
            print(f"Updated client folder_id: {group_id} -> {folder_id}")
        except Exception as ex:
            print(f"Error updating client folder_id: {str(ex)}")

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

    def register_client(self, group_id: str, company_name: str, contacts: List[dict] = None, notes: str = "") -> bool:
        """新規クライアントを登録"""
        try:
            item = {
                'group_id': group_id,
                'company_name': company_name,
                'contacts': contacts or [],
                'notes': notes,
                'status': 'active'
            }
            self.table.put_item(Item=item)
            print(f"Registered new client: {company_name} ({group_id})")
            return True
        except Exception as ex:
            print(f"Error registering client: {str(ex)}")
            return False

    def set_pending_registration(self, group_id: str, suggested_company: str = None):
        """登録待ち状態を設定"""
        try:
            item = {
                'group_id': group_id,
                'status': 'pending_registration',
                'suggested_company': suggested_company or ''
            }
            self.table.put_item(Item=item)
            print(f"Set pending registration: {group_id}")
        except Exception as ex:
            print(f"Error setting pending registration: {str(ex)}")

    def is_pending_registration(self, group_id: str) -> tuple:
        """登録待ち状態かどうかを確認

        Returns:
            (is_pending, suggested_company)
        """
        try:
            response = self.table.get_item(Key={'group_id': group_id})
            item = response.get('Item')
            if item and item.get('status') == 'pending_registration':
                return True, item.get('suggested_company', '')
            return False, ''
        except Exception as ex:
            print(f"Error checking pending registration: {str(ex)}")
            return False, ''

    def get_all_company_names(self) -> List[str]:
        """登録済みの全会社名を取得"""
        try:
            response = self.table.scan(
                FilterExpression='attribute_exists(company_name) AND #s = :active',
                ExpressionAttributeNames={'#s': 'status'},
                ExpressionAttributeValues={':active': 'active'},
                ProjectionExpression='company_name'
            )
            companies = [item['company_name'] for item in response.get('Items', []) if item.get('company_name')]
            return list(set(companies))  # 重複を除去
        except Exception as ex:
            print(f"Error getting company names: {str(ex)}")
            return []

    def find_similar_company(self, input_text: str) -> Optional[str]:
        """入力テキストから類似の会社名を検索"""
        companies = self.get_all_company_names()
        if not companies:
            return None

        # 部分一致で検索
        input_lower = input_text.lower()
        for company in companies:
            if input_lower in company.lower() or company.lower() in input_lower:
                return company

        return None

    def delete_client(self, group_id: str) -> bool:
        """クライアントを削除"""
        try:
            self.table.delete_item(Key={'group_id': group_id})
            print(f"Deleted client: {group_id}")
            return True
        except Exception as ex:
            print(f"Error deleting client: {str(ex)}")
            return False
