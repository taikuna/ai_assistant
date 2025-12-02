"""
未処理メッセージ管理サービス
グループ内でトリガーキーワードが来るまでメッセージを一時保存
"""
import boto3
from boto3.dynamodb.conditions import Key
from datetime import datetime, timedelta
from typing import List, Optional
import re


class UnprocessedMessageService:
    """未処理メッセージを管理するサービス"""

    # トリガーキーワード（これらが含まれたら処理を開始）半角・全角両対応
    TRIGGER_KEYWORDS = ['@ai', '@AI', '@依頼', '＠ai', '＠AI', '＠依頼']

    def __init__(self, table_name: str = None):
        self.dynamodb = boto3.resource('dynamodb')
        self.table = self.dynamodb.Table(table_name or 'ai_secretary_unprocessed_messages')

    def has_trigger_keyword(self, message_text: str) -> bool:
        """メッセージにトリガーキーワードが含まれているか"""
        for keyword in self.TRIGGER_KEYWORDS:
            if keyword in message_text:
                return True
        return False

    def remove_trigger_keyword(self, message_text: str) -> str:
        """メッセージからトリガーキーワードを除去"""
        result = message_text
        for keyword in self.TRIGGER_KEYWORDS:
            result = result.replace(keyword, '')
        return result.strip()

    def save_unprocessed_message(
        self,
        group_id: str,
        message_id: str,
        user_id: str,
        user_name: str,
        message_text: str
    ) -> bool:
        """未処理メッセージを保存"""
        try:
            created_at = datetime.now().isoformat()
            # 1時間後に自動削除（TTL）
            expires_at = int((datetime.now() + timedelta(hours=1)).timestamp())

            self.table.put_item(Item={
                'group_id': group_id,
                'message_id': message_id,
                'user_id': user_id,
                'user_name': user_name,
                'message_text': message_text,
                'created_at': created_at,
                'ttl': expires_at
            })
            print(f"Saved unprocessed message: {message_id} in group {group_id}")
            return True
        except Exception as ex:
            print(f"Save unprocessed message error: {str(ex)}")
            return False

    def get_unprocessed_messages(self, group_id: str) -> List[dict]:
        """グループの未処理メッセージを取得（時系列順）"""
        try:
            response = self.table.query(
                KeyConditionExpression=Key('group_id').eq(group_id)
            )
            items = response.get('Items', [])
            # 作成日時でソート
            items.sort(key=lambda x: x.get('created_at', ''))
            return items
        except Exception as ex:
            print(f"Get unprocessed messages error: {str(ex)}")
            return []

    def delete_unprocessed_messages(self, group_id: str) -> bool:
        """グループの未処理メッセージをすべて削除"""
        try:
            messages = self.get_unprocessed_messages(group_id)
            for msg in messages:
                self.table.delete_item(Key={
                    'group_id': group_id,
                    'message_id': msg['message_id']
                })
            print(f"Deleted {len(messages)} unprocessed messages from group {group_id}")
            return True
        except Exception as ex:
            print(f"Delete unprocessed messages error: {str(ex)}")
            return False

    def combine_messages(self, messages: List[dict], trigger_message: str = None) -> str:
        """未処理メッセージを結合"""
        combined_parts = []

        for msg in messages:
            user_name = msg.get('user_name', 'Unknown')
            text = msg.get('message_text', '')
            combined_parts.append(f"{user_name}: {text}")

        # トリガーメッセージも追加（トリガーキーワードを除去）
        if trigger_message:
            cleaned_trigger = self.remove_trigger_keyword(trigger_message)
            if cleaned_trigger:
                combined_parts.append(cleaned_trigger)

        return '\n'.join(combined_parts)
