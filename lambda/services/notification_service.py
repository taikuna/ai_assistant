"""
通知サービス - Slack等への通知
"""
import json
import urllib.request
import os
from typing import Optional


class SlackNotificationService:
    """Slack通知を行うサービス"""

    def __init__(self, webhook_url: str = None, bot_token: str = None):
        self.webhook_url = webhook_url or os.environ.get('SLACK_WEBHOOK_URL')
        self.bot_token = bot_token or os.environ.get('SLACK_BOT_TOKEN')
        self.approval_channel = os.environ.get('SLACK_APPROVAL_CHANNEL', '#ai-secretary-approval')

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
        """新規依頼の通知を送信 - 現在無効化中（承認リクエストで代替）"""
        # 承認リクエストメッセージで十分なため無効化
        print(f"Order notification skipped (disabled): {order_id}")
        return True

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

    def _build_message_section(self, short_text: str, full_text: str, pending_id: str) -> dict:
        """お客様メッセージセクションを構築（長い場合は全文表示ボタン付き）"""
        section = {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*📩 お客様からのメッセージ:*\n```{short_text}```"
            }
        }
        # 300文字以上の場合はボタンを追加
        if len(full_text) > 300:
            section["accessory"] = {
                "type": "button",
                "text": {"type": "plain_text", "text": "全文を見る", "emoji": True},
                "action_id": "view_full_message",
                "value": pending_id
            }
        return section

    def send_approval_request(
        self,
        pending_id: str,
        customer_name: str,
        company_name: str,
        original_message: str,
        response_text: str,
        channel: str = None,
        has_mention: bool = True
    ) -> bool:
        """承認リクエストをSlackに送信（ボタン付き）"""
        if not self.bot_token:
            print("Slack bot token not configured")
            return False

        try:
            # メッセージを短縮
            original_short = original_message[:300] + "..." if len(original_message) > 300 else original_message
            response_short = response_text[:500] + "..." if len(response_text) > 500 else response_text

            # メンション表示
            mention_text = f"@{customer_name}" if has_mention else "（メンションなし）"

            slack_message = {
                "channel": channel or self.approval_channel,
                "text": f"承認リクエスト: {customer_name}（{company_name}）",
                "blocks": [
                    {
                        "type": "header",
                        "text": {
                            "type": "plain_text",
                            "text": f"📨 承認リクエスト [{pending_id}]",
                            "emoji": True
                        }
                    },
                    {
                        "type": "section",
                        "fields": [
                            {"type": "mrkdwn", "text": f"*お客様:*\n{customer_name}"},
                            {"type": "mrkdwn", "text": f"*会社:*\n{company_name}"},
                            {"type": "mrkdwn", "text": f"*メンション:*\n{mention_text}"}
                        ]
                    },
                    self._build_message_section(original_short, original_message, pending_id),
                    {
                        "type": "divider"
                    },
                    {
                        "type": "section",
                        "text": {
                            "type": "mrkdwn",
                            "text": f"*📤 AI生成の返信:*\n```{response_short}```"
                        }
                    },
                    {
                        "type": "actions",
                        "elements": [
                            {
                                "type": "button",
                                "text": {"type": "plain_text", "text": "✅ 送信", "emoji": True},
                                "style": "primary",
                                "action_id": "approve_send",
                                "value": pending_id
                            },
                            {
                                "type": "button",
                                "text": {"type": "plain_text", "text": "❌ 却下", "emoji": True},
                                "style": "danger",
                                "action_id": "approve_reject",
                                "value": pending_id
                            },
                            {
                                "type": "button",
                                "text": {"type": "plain_text", "text": "✏️ 修正", "emoji": True},
                                "action_id": "approve_edit",
                                "value": pending_id
                            },
                            {
                                "type": "button",
                                "text": {"type": "plain_text", "text": "📦 納品", "emoji": True},
                                "action_id": "create_delivery",
                                "value": pending_id
                            }
                        ]
                    }
                ]
            }

            req = urllib.request.Request(
                "https://slack.com/api/chat.postMessage",
                json.dumps(slack_message).encode('utf-8'),
                {
                    'Content-Type': 'application/json',
                    'Authorization': f'Bearer {self.bot_token}'
                }
            )
            response = urllib.request.urlopen(req)
            result = json.loads(response.read().decode('utf-8'))

            if result.get('ok'):
                print(f"Slack approval request sent: {pending_id}")
                return True
            else:
                print(f"Slack API error: {result.get('error')}")
                return False

        except Exception as ex:
            print(f"Slack approval error: {str(ex)}")
            return False

    def send_delivery_approval_request(
        self,
        pending_id: str,
        customer_name: str,
        company_name: str,
        delivery_message: str,
        channel: str = None,
        has_mention: bool = True
    ) -> bool:
        """納品メッセージの承認リクエストをSlackに送信"""
        if not self.bot_token:
            print("Slack bot token not configured")
            return False

        try:
            # メンション表示
            mention_text = f"@{customer_name}" if has_mention else "（メンションなし）"

            slack_message = {
                "channel": channel or self.approval_channel,
                "text": f"📦 納品確認: {customer_name}（{company_name}）",
                "blocks": [
                    {
                        "type": "header",
                        "text": {
                            "type": "plain_text",
                            "text": f"📦 納品確認 [{pending_id}]",
                            "emoji": True
                        }
                    },
                    {
                        "type": "section",
                        "fields": [
                            {"type": "mrkdwn", "text": f"*お客様:*\n{customer_name}"},
                            {"type": "mrkdwn", "text": f"*会社:*\n{company_name}"},
                            {"type": "mrkdwn", "text": f"*メンション:*\n{mention_text}"}
                        ]
                    },
                    {
                        "type": "divider"
                    },
                    {
                        "type": "section",
                        "text": {
                            "type": "mrkdwn",
                            "text": f"*📤 納品メッセージ:*\n```{delivery_message}```"
                        }
                    },
                    {
                        "type": "actions",
                        "elements": [
                            {
                                "type": "button",
                                "text": {"type": "plain_text", "text": "✅ 送信", "emoji": True},
                                "style": "primary",
                                "action_id": "approve_send",
                                "value": pending_id
                            },
                            {
                                "type": "button",
                                "text": {"type": "plain_text", "text": "❌ 却下", "emoji": True},
                                "style": "danger",
                                "action_id": "approve_reject",
                                "value": pending_id
                            },
                            {
                                "type": "button",
                                "text": {"type": "plain_text", "text": "✏️ 修正", "emoji": True},
                                "action_id": "approve_edit",
                                "value": pending_id
                            }
                        ]
                    }
                ]
            }

            req = urllib.request.Request(
                "https://slack.com/api/chat.postMessage",
                json.dumps(slack_message).encode('utf-8'),
                {
                    'Content-Type': 'application/json',
                    'Authorization': f'Bearer {self.bot_token}'
                }
            )
            response = urllib.request.urlopen(req)
            result = json.loads(response.read().decode('utf-8'))

            if result.get('ok'):
                print(f"Slack delivery approval request sent: {pending_id}")
                return True
            else:
                print(f"Slack API error: {result.get('error')}")
                return False

        except Exception as ex:
            print(f"Slack delivery approval error: {str(ex)}")
            return False

    def update_approval_message(
        self,
        channel: str,
        message_ts: str,
        pending_id: str,
        status: str,
        action_user: str = None,
        response_text: str = None,
        customer_name: str = None,
        company_name: str = None,
        original_message: str = None
    ) -> bool:
        """承認メッセージを更新（送信取り消し対応）"""
        if not self.bot_token:
            return False

        try:
            status_text = {
                'approved': '✅ 送信済み',
                'rejected': '❌ 却下済み',
                'editing': '✏️ 修正中',
                'reopened': '🔄 再編集中'
            }.get(status, status)

            blocks = [
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"*承認リクエスト [{pending_id}]*\n{status_text}" + (f" by <@{action_user}>" if action_user else "")
                    }
                }
            ]

            # 送信済みの場合は納品ボタンと送信取り消しボタンを追加
            if status == 'approved':
                blocks.append({
                    "type": "actions",
                    "elements": [
                        {
                            "type": "button",
                            "text": {"type": "plain_text", "text": "📦 納品", "emoji": True},
                            "action_id": "create_delivery",
                            "value": pending_id
                        },
                        {
                            "type": "button",
                            "text": {"type": "plain_text", "text": "↩️ 送信取り消し", "emoji": True},
                            "style": "danger",
                            "action_id": "unsend_message",
                            "value": pending_id
                        }
                    ]
                })

            slack_message = {
                "channel": channel,
                "ts": message_ts,
                "text": f"承認リクエスト [{pending_id}] - {status_text}",
                "blocks": blocks
            }

            req = urllib.request.Request(
                "https://slack.com/api/chat.update",
                json.dumps(slack_message).encode('utf-8'),
                {
                    'Content-Type': 'application/json',
                    'Authorization': f'Bearer {self.bot_token}'
                }
            )
            response = urllib.request.urlopen(req)
            result = json.loads(response.read().decode('utf-8'))
            return result.get('ok', False)

        except Exception as ex:
            print(f"Slack update error: {str(ex)}")
            return False

    def open_edit_modal(
        self,
        trigger_id: str,
        pending_id: str,
        current_text: str
    ) -> bool:
        """修正用モーダルを開く"""
        if not self.bot_token:
            return False

        try:
            modal = {
                "trigger_id": trigger_id,
                "view": {
                    "type": "modal",
                    "callback_id": "edit_response_modal",
                    "private_metadata": pending_id,
                    "title": {"type": "plain_text", "text": "返信を修正"},
                    "submit": {"type": "plain_text", "text": "送信"},
                    "close": {"type": "plain_text", "text": "キャンセル"},
                    "blocks": [
                        {
                            "type": "input",
                            "block_id": "response_block",
                            "element": {
                                "type": "plain_text_input",
                                "action_id": "response_text",
                                "multiline": True,
                                "initial_value": current_text
                            },
                            "label": {"type": "plain_text", "text": "返信内容"}
                        },
                        {
                            "type": "input",
                            "block_id": "prompt_block",
                            "optional": True,
                            "element": {
                                "type": "plain_text_input",
                                "action_id": "prompt_text",
                                "multiline": True,
                                "placeholder": {"type": "plain_text", "text": "例: もっと丁寧に、納期を強調して"}
                            },
                            "label": {"type": "plain_text", "text": "AIへの修正指示（オプション）"}
                        }
                    ]
                }
            }

            req = urllib.request.Request(
                "https://slack.com/api/views.open",
                json.dumps(modal).encode('utf-8'),
                {
                    'Content-Type': 'application/json',
                    'Authorization': f'Bearer {self.bot_token}'
                }
            )
            response = urllib.request.urlopen(req)
            result = json.loads(response.read().decode('utf-8'))
            if result.get('ok'):
                print(f"Slack modal opened: {pending_id}")
                return True
            else:
                print(f"Slack modal error: {result.get('error')}")
                return False

        except Exception as ex:
            print(f"Slack modal exception: {str(ex)}")
            return False

    def open_full_message_modal(
        self,
        trigger_id: str,
        pending_id: str,
        customer_name: str,
        original_message: str
    ) -> bool:
        """お客様メッセージの全文を表示するモーダルを開く"""
        if not self.bot_token:
            return False

        try:
            modal = {
                "trigger_id": trigger_id,
                "view": {
                    "type": "modal",
                    "title": {"type": "plain_text", "text": "お客様からのメッセージ"},
                    "close": {"type": "plain_text", "text": "閉じる"},
                    "blocks": [
                        {
                            "type": "section",
                            "text": {
                                "type": "mrkdwn",
                                "text": f"*送信者:* {customer_name}"
                            }
                        },
                        {
                            "type": "divider"
                        },
                        {
                            "type": "section",
                            "text": {
                                "type": "mrkdwn",
                                "text": original_message[:3000]  # Slackの制限
                            }
                        }
                    ]
                }
            }

            req = urllib.request.Request(
                "https://slack.com/api/views.open",
                json.dumps(modal).encode('utf-8'),
                {
                    'Content-Type': 'application/json',
                    'Authorization': f'Bearer {self.bot_token}'
                }
            )
            response = urllib.request.urlopen(req)
            result = json.loads(response.read().decode('utf-8'))
            if result.get('ok'):
                print(f"Full message modal opened: {pending_id}")
                return True
            else:
                print(f"Full message modal error: {result.get('error')}")
                return False

        except Exception as ex:
            print(f"Full message modal exception: {str(ex)}")
            return False
