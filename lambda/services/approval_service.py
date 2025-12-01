"""
承認フローサービス - AIの返信を承認してから送信
"""
import json
import os
import uuid
from datetime import datetime, timedelta
from typing import Optional, Tuple

import boto3
from boto3.dynamodb.conditions import Key, Attr


class ApprovalService:
    """返信の承認フローを管理するサービス"""

    def __init__(self, table_name: str = None):
        self.dynamodb = boto3.resource('dynamodb')
        self.table = self.dynamodb.Table(table_name or 'ai_secretary_pending_messages')
        # 確認用グループID（環境変数で設定）
        self.approval_group_id = os.environ.get('APPROVAL_GROUP_ID', '')

    def is_approval_enabled(self) -> bool:
        """承認フローが有効かどうか"""
        return bool(self.approval_group_id)

    def is_approval_group(self, group_id: str) -> bool:
        """確認用グループかどうか"""
        return group_id == self.approval_group_id

    def save_pending_message(
        self,
        target_id: str,
        target_type: str,  # 'group' or 'user'
        response_text: str,
        customer_name: str,
        company_name: str,
        original_message: str,
        order_id: str = None
    ) -> str:
        """承認待ちメッセージを保存

        Returns:
            pending_id: 保留メッセージID
        """
        pending_id = str(uuid.uuid4())[:8]  # 短いIDで扱いやすく
        created_at = datetime.now().isoformat()
        expires_at = (datetime.now() + timedelta(hours=24)).isoformat()

        item = {
            'pending_id': pending_id,
            'created_at': created_at,
            'expires_at': expires_at,
            'target_id': target_id,
            'target_type': target_type,
            'response_text': response_text,
            'customer_name': customer_name,
            'company_name': company_name,
            'original_message': original_message[:500],  # 長すぎる場合は切り詰め
            'order_id': order_id or '',
            'status': 'pending'  # pending, approved, rejected
        }

        self.table.put_item(Item=item)
        print(f"Pending message saved: {pending_id}")
        return pending_id

    def get_pending_message(self, pending_id: str) -> Optional[dict]:
        """保留メッセージを取得"""
        try:
            response = self.table.scan(
                FilterExpression=Attr('pending_id').eq(pending_id) & Attr('status').eq('pending'),
                Limit=1
            )
            items = response.get('Items', [])
            return items[0] if items else None
        except Exception as ex:
            print(f"Get pending error: {str(ex)}")
            return None

    def approve_message(self, pending_id: str, edited_text: str = None) -> Optional[dict]:
        """メッセージを承認

        Args:
            pending_id: 保留メッセージID
            edited_text: 編集されたテキスト（Noneならオリジナルを使用）

        Returns:
            承認されたメッセージ情報
        """
        pending = self.get_pending_message(pending_id)
        if not pending:
            return None

        # ステータスを更新
        self.table.update_item(
            Key={'pending_id': pending_id, 'created_at': pending['created_at']},
            UpdateExpression='SET #status = :status, approved_at = :approved_at',
            ExpressionAttributeNames={'#status': 'status'},
            ExpressionAttributeValues={
                ':status': 'approved',
                ':approved_at': datetime.now().isoformat()
            }
        )

        # 編集があれば編集後のテキストを返す
        if edited_text:
            pending['response_text'] = edited_text

        return pending

    def reject_message(self, pending_id: str) -> bool:
        """メッセージを却下"""
        pending = self.get_pending_message(pending_id)
        if not pending:
            return False

        self.table.update_item(
            Key={'pending_id': pending_id, 'created_at': pending['created_at']},
            UpdateExpression='SET #status = :status',
            ExpressionAttributeNames={'#status': 'status'},
            ExpressionAttributeValues={':status': 'rejected'}
        )
        return True

    def update_pending_response(self, pending_id: str, new_response: str) -> bool:
        """保留メッセージの返信テキストを更新"""
        pending = self.get_pending_message(pending_id)
        if not pending:
            return False

        self.table.update_item(
            Key={'pending_id': pending_id, 'created_at': pending['created_at']},
            UpdateExpression='SET response_text = :response_text',
            ExpressionAttributeValues={':response_text': new_response}
        )
        return True

    def get_latest_pending(self) -> Optional[dict]:
        """最新の保留メッセージを取得"""
        try:
            response = self.table.scan(
                FilterExpression=Attr('status').eq('pending')
            )
            items = response.get('Items', [])
            if items:
                items.sort(key=lambda x: x['created_at'], reverse=True)
                return items[0]
            return None
        except Exception as ex:
            print(f"Get latest pending error: {str(ex)}")
            return None


class FlexMessageBuilder:
    """LINE Flex Messageを構築するヘルパー"""

    @staticmethod
    def build_approval_message(
        pending_id: str,
        customer_name: str,
        company_name: str,
        original_message: str,
        response_text: str
    ) -> dict:
        """承認用Flex Messageを構築"""
        return {
            "type": "flex",
            "altText": f"【承認依頼】{company_name}への返信",
            "contents": {
                "type": "bubble",
                "size": "giga",
                "header": {
                    "type": "box",
                    "layout": "vertical",
                    "contents": [
                        {
                            "type": "text",
                            "text": "返信承認依頼",
                            "weight": "bold",
                            "size": "lg",
                            "color": "#1DB446"
                        },
                        {
                            "type": "text",
                            "text": f"ID: {pending_id}",
                            "size": "xs",
                            "color": "#999999"
                        }
                    ],
                    "paddingAll": "15px",
                    "backgroundColor": "#F5F5F5"
                },
                "body": {
                    "type": "box",
                    "layout": "vertical",
                    "contents": [
                        # 宛先情報
                        {
                            "type": "box",
                            "layout": "horizontal",
                            "contents": [
                                {
                                    "type": "text",
                                    "text": "宛先",
                                    "size": "sm",
                                    "color": "#555555",
                                    "flex": 1
                                },
                                {
                                    "type": "text",
                                    "text": f"{customer_name}（{company_name}）",
                                    "size": "sm",
                                    "color": "#111111",
                                    "flex": 4,
                                    "wrap": True
                                }
                            ],
                            "margin": "md"
                        },
                        # セパレータ
                        {
                            "type": "separator",
                            "margin": "lg"
                        },
                        # 元メッセージ
                        {
                            "type": "text",
                            "text": "お客様のメッセージ",
                            "size": "xs",
                            "color": "#AAAAAA",
                            "margin": "lg"
                        },
                        {
                            "type": "text",
                            "text": original_message[:200] + ("..." if len(original_message) > 200 else ""),
                            "size": "sm",
                            "color": "#666666",
                            "wrap": True,
                            "margin": "sm"
                        },
                        # セパレータ
                        {
                            "type": "separator",
                            "margin": "lg"
                        },
                        # AI返信案
                        {
                            "type": "text",
                            "text": "AIの返信案",
                            "size": "xs",
                            "color": "#AAAAAA",
                            "margin": "lg"
                        },
                        {
                            "type": "text",
                            "text": response_text,
                            "size": "sm",
                            "color": "#111111",
                            "wrap": True,
                            "margin": "sm"
                        }
                    ],
                    "paddingAll": "15px"
                },
                "footer": {
                    "type": "box",
                    "layout": "vertical",
                    "contents": [
                        {
                            "type": "box",
                            "layout": "horizontal",
                            "contents": [
                                {
                                    "type": "button",
                                    "action": {
                                        "type": "message",
                                        "label": "送信",
                                        "text": f"送信 {pending_id}"
                                    },
                                    "style": "primary",
                                    "color": "#1DB446",
                                    "height": "sm",
                                    "flex": 1
                                },
                                {
                                    "type": "button",
                                    "action": {
                                        "type": "message",
                                        "label": "却下",
                                        "text": f"却下 {pending_id}"
                                    },
                                    "style": "secondary",
                                    "height": "sm",
                                    "flex": 1,
                                    "margin": "sm"
                                }
                            ],
                            "spacing": "sm"
                        },
                        {
                            "type": "text",
                            "text": "編集する場合は編集内容を入力後「編集送信 ID」",
                            "size": "xxs",
                            "color": "#AAAAAA",
                            "margin": "md",
                            "align": "center"
                        }
                    ],
                    "paddingAll": "15px"
                }
            }
        }

    @staticmethod
    def build_revised_message(
        pending_id: str,
        customer_name: str,
        company_name: str,
        original_response: str,
        revised_response: str,
        revision_instruction: str
    ) -> dict:
        """修正後の返信案を表示するFlex Message"""
        return {
            "type": "flex",
            "altText": f"【修正案】{company_name}への返信",
            "contents": {
                "type": "bubble",
                "size": "giga",
                "header": {
                    "type": "box",
                    "layout": "vertical",
                    "contents": [
                        {
                            "type": "text",
                            "text": "修正後の返信案",
                            "weight": "bold",
                            "size": "lg",
                            "color": "#FF8C00"
                        },
                        {
                            "type": "text",
                            "text": f"ID: {pending_id}",
                            "size": "xs",
                            "color": "#999999"
                        }
                    ],
                    "paddingAll": "15px",
                    "backgroundColor": "#FFF8DC"
                },
                "body": {
                    "type": "box",
                    "layout": "vertical",
                    "contents": [
                        # 宛先情報
                        {
                            "type": "box",
                            "layout": "horizontal",
                            "contents": [
                                {
                                    "type": "text",
                                    "text": "宛先",
                                    "size": "sm",
                                    "color": "#555555",
                                    "flex": 1
                                },
                                {
                                    "type": "text",
                                    "text": f"{customer_name}（{company_name}）",
                                    "size": "sm",
                                    "color": "#111111",
                                    "flex": 4,
                                    "wrap": True
                                }
                            ],
                            "margin": "md"
                        },
                        # セパレータ
                        {
                            "type": "separator",
                            "margin": "lg"
                        },
                        # 修正指示
                        {
                            "type": "text",
                            "text": "修正指示",
                            "size": "xs",
                            "color": "#AAAAAA",
                            "margin": "lg"
                        },
                        {
                            "type": "text",
                            "text": revision_instruction,
                            "size": "sm",
                            "color": "#FF8C00",
                            "wrap": True,
                            "margin": "sm"
                        },
                        # セパレータ
                        {
                            "type": "separator",
                            "margin": "lg"
                        },
                        # 修正後の返信
                        {
                            "type": "text",
                            "text": "修正後の返信案",
                            "size": "xs",
                            "color": "#AAAAAA",
                            "margin": "lg"
                        },
                        {
                            "type": "text",
                            "text": revised_response,
                            "size": "sm",
                            "color": "#111111",
                            "wrap": True,
                            "margin": "sm"
                        }
                    ],
                    "paddingAll": "15px"
                },
                "footer": {
                    "type": "box",
                    "layout": "vertical",
                    "contents": [
                        {
                            "type": "box",
                            "layout": "horizontal",
                            "contents": [
                                {
                                    "type": "button",
                                    "action": {
                                        "type": "message",
                                        "label": "送信",
                                        "text": f"送信 {pending_id}"
                                    },
                                    "style": "primary",
                                    "color": "#1DB446",
                                    "height": "sm",
                                    "flex": 1
                                },
                                {
                                    "type": "button",
                                    "action": {
                                        "type": "message",
                                        "label": "却下",
                                        "text": f"却下 {pending_id}"
                                    },
                                    "style": "secondary",
                                    "height": "sm",
                                    "flex": 1,
                                    "margin": "sm"
                                }
                            ],
                            "spacing": "sm"
                        },
                        {
                            "type": "text",
                            "text": "さらに修正する場合は「修正 ID：指示内容」",
                            "size": "xxs",
                            "color": "#AAAAAA",
                            "margin": "md",
                            "align": "center"
                        }
                    ],
                    "paddingAll": "15px"
                }
            }
        }

    @staticmethod
    def build_result_message(success: bool, action: str, target_info: str) -> dict:
        """結果通知用Flex Messageを構築"""
        if success:
            color = "#1DB446"
            icon = "check"
            title = f"{action}完了"
        else:
            color = "#FF5551"
            icon = "close"
            title = f"{action}失敗"

        return {
            "type": "flex",
            "altText": title,
            "contents": {
                "type": "bubble",
                "size": "kilo",
                "body": {
                    "type": "box",
                    "layout": "horizontal",
                    "contents": [
                        {
                            "type": "text",
                            "text": "OK" if success else "NG",
                            "size": "xl",
                            "weight": "bold",
                            "color": color
                        },
                        {
                            "type": "box",
                            "layout": "vertical",
                            "contents": [
                                {
                                    "type": "text",
                                    "text": title,
                                    "size": "sm",
                                    "weight": "bold"
                                },
                                {
                                    "type": "text",
                                    "text": target_info,
                                    "size": "xs",
                                    "color": "#888888"
                                }
                            ],
                            "margin": "md"
                        }
                    ],
                    "paddingAll": "15px"
                }
            }
        }
