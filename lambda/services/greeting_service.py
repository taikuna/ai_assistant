"""
挨拶サービス - その日最初のメッセージに挨拶を追加
"""
import boto3
from datetime import datetime, date
from typing import Optional
import os


class GreetingService:
    """その日最初のやり取りに挨拶を追加するサービス"""

    def __init__(self, table_name: str = None):
        self.dynamodb = boto3.resource('dynamodb')
        self.table = self.dynamodb.Table(table_name or 'ai_secretary_last_contact')

    def should_greet(self, group_id: Optional[str], user_id: str) -> bool:
        """挨拶が必要かどうか判定（その日初めてのやり取りか）"""
        try:
            # グループIDがあればグループ単位、なければユーザー単位で判定
            contact_id = group_id if group_id else f"user_{user_id}"
            today = date.today().isoformat()

            response = self.table.get_item(Key={'contact_id': contact_id})
            item = response.get('Item')

            if not item:
                # 初めてのやり取り
                return True

            last_date = item.get('last_contact_date', '')
            return last_date != today

        except Exception as ex:
            print(f"Greeting check error: {str(ex)}")
            # エラー時は挨拶しない（安全側に倒す）
            return False

    def record_contact(self, group_id: Optional[str], user_id: str):
        """やり取りを記録"""
        try:
            contact_id = group_id if group_id else f"user_{user_id}"
            today = date.today().isoformat()
            now = datetime.now().isoformat()

            self.table.put_item(Item={
                'contact_id': contact_id,
                'last_contact_date': today,
                'last_contact_time': now,
                'user_id': user_id
            })

        except Exception as ex:
            print(f"Record contact error: {str(ex)}")

    def create_greeting(self, company_name: Optional[str], user_name: str) -> str:
        """挨拶メッセージを生成"""
        if company_name and company_name != "未登録クライアント":
            return f"{company_name}\n{user_name}様\n\nいつもお世話になっております！\n合同会社四次元のAIです。\n\n"
        else:
            return f"{user_name}様\n\nいつもお世話になっております！\n合同会社四次元のAIです。\n\n"

    def add_greeting_if_needed(
        self,
        response_text: str,
        group_id: Optional[str],
        user_id: str,
        company_name: Optional[str] = None,
        user_name: str = ""
    ) -> str:
        """必要に応じて挨拶を追加"""
        if self.should_greet(group_id, user_id):
            self.record_contact(group_id, user_id)
            greeting = self.create_greeting(company_name, user_name)
            return greeting + response_text
        return response_text
