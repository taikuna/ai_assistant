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

    def record_contact(self, group_id: Optional[str], user_id: str, ai_response: str = ""):
        """やり取りを記録

        Args:
            group_id: グループID
            user_id: ユーザーID
            ai_response: AIの返信内容（返信待ち判定用）
        """
        try:
            contact_id = group_id if group_id else f"user_{user_id}"
            today = date.today().isoformat()
            now = datetime.now().isoformat()

            item = {
                'contact_id': contact_id,
                'last_contact_date': today,
                'last_contact_time': now,
                'user_id': user_id
            }

            # AIの返信内容を保存（500文字まで）
            if ai_response:
                item['last_ai_response'] = ai_response[:500]

            self.table.put_item(Item=item)

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

    def is_awaiting_reply(self, group_id: Optional[str], user_id: str, minutes: int = 10) -> tuple:
        """AIが直近で返信していて、返信待ち状態かどうか

        直近N分以内にAIが返信していれば、ユーザーの次のメッセージは
        トリガーキーワードなしでも処理する

        Returns:
            (is_awaiting: bool, last_ai_response: str)
        """
        try:
            from datetime import timedelta

            contact_id = group_id if group_id else f"user_{user_id}"

            response = self.table.get_item(Key={'contact_id': contact_id})
            item = response.get('Item')

            if not item:
                return False, ""

            last_contact_time = item.get('last_contact_time')
            if not last_contact_time:
                return False, ""

            # 最後の返信時間からN分以内かチェック
            last_time = datetime.fromisoformat(last_contact_time)
            cutoff_time = datetime.now() - timedelta(minutes=minutes)

            is_awaiting = last_time > cutoff_time
            last_ai_response = item.get('last_ai_response', '') if is_awaiting else ""

            return is_awaiting, last_ai_response

        except Exception as ex:
            print(f"Awaiting reply check error: {str(ex)}")
            return False, ""
