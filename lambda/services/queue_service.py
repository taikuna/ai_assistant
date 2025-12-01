"""
SQSキューサービス - 非同期ファイル処理用
"""
import json
import os
import boto3


class QueueService:
    """SQSにタスクをキューするサービス"""

    def __init__(self, queue_url: str = None):
        self.sqs = boto3.client('sqs')
        self.queue_url = queue_url or os.environ.get(
            'FILE_PROCESSOR_QUEUE_URL',
            'https://sqs.ap-northeast-1.amazonaws.com/461690068687/ai-secretary-file-processor'
        )

    def queue_attachment_processing(
        self,
        order_id: str,
        order_created_at: str,
        folder_id: str,
        project_name: str,
        attachments: list,
        target_id: str,
        is_group: bool,
        company_folder_id: str = None,
        user_name: str = ''
    ):
        """LINE添付ファイル処理タスクをキュー"""
        message = {
            'task_type': 'process_attachments',
            'order_id': order_id,
            'order_created_at': order_created_at,
            'folder_id': folder_id,
            'project_name': project_name,
            'attachments': attachments,
            'target_id': target_id,
            'is_group': is_group,
            'company_folder_id': company_folder_id,
            'user_name': user_name
        }

        self._send_message(message)
        print(f"Queued attachment processing for order: {order_id}")

    def queue_url_processing(
        self,
        order_id: str,
        order_created_at: str,
        folder_id: str,
        project_name: str,
        urls: list,
        target_id: str,
        is_group: bool
    ):
        """URLダウンロード処理タスクをキュー"""
        message = {
            'task_type': 'process_urls',
            'order_id': order_id,
            'order_created_at': order_created_at,
            'folder_id': folder_id,
            'project_name': project_name,
            'urls': urls,
            'target_id': target_id,
            'is_group': is_group
        }

        self._send_message(message)
        print(f"Queued URL processing for order: {order_id}")

    def _send_message(self, message: dict):
        """SQSにメッセージを送信"""
        try:
            self.sqs.send_message(
                QueueUrl=self.queue_url,
                MessageBody=json.dumps(message)
            )
        except Exception as ex:
            print(f"SQS send error: {str(ex)}")
