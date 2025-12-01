"""
遅延返信サービス - 1分後に返信（取り消し対応）
EventBridge Scheduler または DynamoDB TTL + Lambda を使用
"""
import json
import boto3
import os
from datetime import datetime, timedelta
from typing import Optional

# DynamoDBに保留中のメッセージを保存し、別のLambdaで1分後に処理する方式


class DelayedResponseService:
    """遅延返信を管理するサービス"""

    def __init__(self, table_name: str = None):
        self.dynamodb = boto3.resource('dynamodb')
        self.table = self.dynamodb.Table(table_name or 'ai_secretary_pending_messages')
        self.sqs = boto3.client('sqs')
        self.queue_url = os.environ.get('DELAYED_RESPONSE_QUEUE_URL')

    def queue_delayed_response(
        self,
        message_id: str,
        user_id: str,
        group_id: Optional[str],
        response_text: str,
        platform: str = 'line',
        delay_seconds: int = 60
    ) -> bool:
        """遅延返信をキューに追加"""
        try:
            scheduled_at = datetime.now() + timedelta(seconds=delay_seconds)

            # DynamoDBに保存（処理状態管理用）
            item = {
                'message_id': message_id,
                'user_id': user_id,
                'group_id': group_id or 'none',
                'response_text': response_text,
                'platform': platform,
                'status': 'pending',  # pending, cancelled, sent
                'created_at': datetime.now().isoformat(),
                'scheduled_at': scheduled_at.isoformat(),
                'ttl': int((datetime.now() + timedelta(hours=24)).timestamp())  # 24時間後に自動削除
            }
            self.table.put_item(Item=item)

            # SQSに遅延メッセージを送信
            if self.queue_url:
                self.sqs.send_message(
                    QueueUrl=self.queue_url,
                    MessageBody=json.dumps({
                        'message_id': message_id,
                        'action': 'send_response'
                    }),
                    DelaySeconds=min(delay_seconds, 900)  # SQSの最大は15分
                )

            print(f"Queued delayed response for message: {message_id}")
            return True

        except Exception as ex:
            print(f"Queue Error: {str(ex)}")
            return False

    def cancel_response(self, message_id: str) -> bool:
        """返信をキャンセル（取り消し時）"""
        try:
            self.table.update_item(
                Key={'message_id': message_id},
                UpdateExpression='SET #status = :status',
                ExpressionAttributeNames={'#status': 'status'},
                ExpressionAttributeValues={':status': 'cancelled'}
            )
            print(f"Cancelled response for message: {message_id}")
            return True
        except Exception as ex:
            print(f"Cancel Error: {str(ex)}")
            return False

    def get_pending_response(self, message_id: str) -> Optional[dict]:
        """保留中の返信を取得"""
        try:
            response = self.table.get_item(Key={'message_id': message_id})
            item = response.get('Item')
            if item and item.get('status') == 'pending':
                return item
            return None
        except Exception as ex:
            print(f"Get Pending Error: {str(ex)}")
            return None

    def mark_as_sent(self, message_id: str) -> bool:
        """送信済みとしてマーク"""
        try:
            self.table.update_item(
                Key={'message_id': message_id},
                UpdateExpression='SET #status = :status, sent_at = :sent_at',
                ExpressionAttributeNames={'#status': 'status'},
                ExpressionAttributeValues={
                    ':status': 'sent',
                    ':sent_at': datetime.now().isoformat()
                }
            )
            return True
        except Exception as ex:
            print(f"Mark Sent Error: {str(ex)}")
            return False


class LinePushService:
    """LINE Push Message送信サービス（reply tokenなしで送信）"""

    def __init__(self, channel_access_token: str = None):
        self.channel_access_token = channel_access_token or os.environ.get('LINE_CHANNEL_ACCESS_TOKEN')

    def push_message(self, user_id: str, text: str) -> bool:
        """ユーザーにpushメッセージを送信"""
        import urllib.request

        url = 'https://api.line.me/v2/bot/message/push'
        headers = {
            'Content-Type': 'application/json',
            'Authorization': f'Bearer {self.channel_access_token}'
        }
        data = {
            'to': user_id,
            'messages': [{'type': 'text', 'text': text[:5000]}]
        }

        try:
            req = urllib.request.Request(url, json.dumps(data).encode(), headers)
            urllib.request.urlopen(req)
            print(f"Push message sent to: {user_id}")
            return True
        except Exception as ex:
            print(f"Push Error: {str(ex)}")
            return False

    def push_to_group(self, group_id: str, text: str) -> bool:
        """グループにpushメッセージを送信"""
        import urllib.request

        url = 'https://api.line.me/v2/bot/message/push'
        headers = {
            'Content-Type': 'application/json',
            'Authorization': f'Bearer {self.channel_access_token}'
        }
        data = {
            'to': group_id,
            'messages': [{'type': 'text', 'text': text[:5000]}]
        }

        try:
            req = urllib.request.Request(url, json.dumps(data).encode(), headers)
            urllib.request.urlopen(req)
            print(f"Push message sent to group: {group_id}")
            return True
        except Exception as ex:
            print(f"Push to Group Error: {str(ex)}")
            return False

    def push_flex_to_group(self, group_id: str, flex_message: dict) -> bool:
        """グループにFlex Messageをpush送信"""
        import urllib.request

        url = 'https://api.line.me/v2/bot/message/push'
        headers = {
            'Content-Type': 'application/json',
            'Authorization': f'Bearer {self.channel_access_token}'
        }
        data = {
            'to': group_id,
            'messages': [flex_message]
        }

        try:
            req = urllib.request.Request(url, json.dumps(data).encode(), headers)
            urllib.request.urlopen(req)
            print(f"Push flex message sent to group: {group_id}")
            return True
        except Exception as ex:
            print(f"Push Flex to Group Error: {str(ex)}")
            return False
