"""
通知サービス - Slack等への通知
"""
import json
import urllib.request
import os
from typing import Optional


class SlackNotificationService:
    """Slack通知を行うサービス"""

    def __init__(self, webhook_url: str = None):
        self.webhook_url = webhook_url or os.environ.get('SLACK_WEBHOOK_URL')

    def send_order_notification(
        self,
        order_id: str,
        customer_name: str,
        summary: str,
        deadline: Optional[str] = None,
        folder_url: Optional[str] = None,
        company_name: Optional[str] = None,
        group_id: Optional[str] = None,
        is_registered: bool = True
    ) -> bool:
        """新規依頼の通知を送信"""
        if not self.webhook_url:
            print("Slack webhook URL not configured")
            return False

        try:
            # 会社名はクライアントマスターから取得した値を優先
            company = company_name if company_name else customer_name

            # 未登録クライアントの場合は警告を追加
            header_text = "📥 新規依頼が届きました"
            if not is_registered:
                header_text = "⚠️ 未登録クライアントから依頼"

            slack_message = {
                "blocks": [
                    {
                        "type": "header",
                        "text": {
                            "type": "plain_text",
                            "text": header_text,
                            "emoji": True
                        }
                    },
                    {
                        "type": "section",
                        "fields": [
                            {"type": "mrkdwn", "text": f"*依頼ID:*\n{order_id[:8]}"},
                            {"type": "mrkdwn", "text": f"*お客様:*\n{customer_name}"},
                            {"type": "mrkdwn", "text": f"*会社:*\n{company}"},
                            {"type": "mrkdwn", "text": f"*納期:*\n{deadline if deadline else '未設定'}"}
                        ]
                    },
                    {
                        "type": "section",
                        "text": {
                            "type": "mrkdwn",
                            "text": f"*📋 サマリー:*\n{summary}"
                        }
                    }
                ]
            }

            # 未登録の場合はグループIDを表示（登録用）
            if not is_registered and group_id:
                slack_message["blocks"].append({
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"*🔑 登録用グループID:*\n`{group_id}`"
                    }
                })

            if folder_url:
                slack_message["blocks"].append({
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"📁 <{folder_url}|Google Driveフォルダ>"
                    }
                })

            req = urllib.request.Request(
                self.webhook_url,
                json.dumps(slack_message).encode('utf-8'),
                {'Content-Type': 'application/json'}
            )
            urllib.request.urlopen(req)
            print(f"Slack notification sent for order: {order_id}")
            return True

        except Exception as ex:
            print(f"Slack Error: {str(ex)}")
            return False
