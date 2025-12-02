"""
ユーザーマッピングサービス - グループ内のLINE表示名とユーザーIDを紐付け
"""
from datetime import datetime
from typing import Optional

import boto3
from boto3.dynamodb.conditions import Key


class UserMappingService:
    """グループ内のユーザー名とユーザーIDのマッピングを管理"""

    def __init__(self, table_name: str = None):
        self.dynamodb = boto3.resource('dynamodb')
        self.table = self.dynamodb.Table(table_name or 'ai_secretary_user_mapping')

    def save_user_mapping(
        self,
        group_id: str,
        user_id: str,
        user_name: str
    ) -> bool:
        """ユーザーマッピングを保存（発言時に自動で呼ばれる）

        Args:
            group_id: グループID
            user_id: LINEユーザーID
            user_name: LINE表示名

        Returns:
            成功したかどうか
        """
        if not group_id or not user_id or not user_name:
            return False

        try:
            self.table.put_item(Item={
                'group_id': group_id,
                'user_name': user_name,
                'user_id': user_id,
                'updated_at': datetime.now().isoformat()
            })
            print(f"User mapping saved: {user_name} -> {user_id} in {group_id}")
            return True
        except Exception as ex:
            print(f"Save user mapping error: {str(ex)}")
            return False

    def get_user_id_by_name(
        self,
        group_id: str,
        user_name: str
    ) -> Optional[str]:
        """名前からユーザーIDを取得

        Args:
            group_id: グループID
            user_name: LINE表示名

        Returns:
            ユーザーID（見つからない場合はNone）
        """
        try:
            response = self.table.get_item(Key={
                'group_id': group_id,
                'user_name': user_name
            })
            item = response.get('Item')
            if item:
                return item.get('user_id')
            return None
        except Exception as ex:
            print(f"Get user mapping error: {str(ex)}")
            return None

    def get_all_users_in_group(self, group_id: str) -> list:
        """グループ内の全ユーザーマッピングを取得

        Args:
            group_id: グループID

        Returns:
            ユーザーマッピングのリスト
        """
        try:
            response = self.table.query(
                KeyConditionExpression=Key('group_id').eq(group_id)
            )
            return response.get('Items', [])
        except Exception as ex:
            print(f"Get all users error: {str(ex)}")
            return []

    def search_user_by_partial_name(
        self,
        group_id: str,
        partial_name: str
    ) -> Optional[dict]:
        """部分一致で名前を検索

        Args:
            group_id: グループID
            partial_name: 名前の一部

        Returns:
            マッチしたユーザー情報（複数ある場合は最初の1件）
        """
        users = self.get_all_users_in_group(group_id)
        for user in users:
            if partial_name in user.get('user_name', ''):
                return user
        return None
