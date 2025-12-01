"""
依頼管理サービス - DynamoDB操作
"""
import uuid
import os
from datetime import datetime, timedelta
from typing import List, Optional

import boto3
from boto3.dynamodb.conditions import Key, Attr


class OrderService:
    """依頼データを管理するサービス"""

    def __init__(self, table_name: str = None):
        self.dynamodb = boto3.resource('dynamodb')
        self.table = self.dynamodb.Table(table_name or 'ai_secretary_orders')

    def save_order(
        self,
        user_id: str,
        user_name: str,
        message: str,
        group_id: Optional[str],
        urls: List[str],
        project_name: str = ""
    ) -> tuple:
        """依頼を保存

        Returns:
            (order_id, created_at) のタプル
        """
        order_id = str(uuid.uuid4())
        created_at = datetime.now().isoformat()

        company = ""
        if " - " in user_name:
            company = user_name.split(" - ")[-1]

        item = {
            'order_id': order_id,
            'created_at': created_at,
            'customer_id': user_id,
            'customer_name': user_name,
            'company': company,
            'group_id': group_id or 'unknown',
            'message': message,
            'status': 'received',
            'deadline': None,
            'service_type': self._detect_service_type(message),
            'source_urls': urls if urls else [],
            'project_name': project_name,
        }

        self.table.put_item(Item=item)
        print(f"Order saved: {order_id}, project: {project_name}")
        return order_id, created_at

    def update_order(self, order_id: str, updates: dict, created_at: str = None) -> bool:
        """依頼を更新

        Args:
            order_id: 依頼ID
            updates: 更新する項目
            created_at: 作成日時（複合キーのため必要。省略時はscanで検索）
        """
        try:
            # created_atがない場合はscanで検索
            if not created_at:
                response = self.table.scan(
                    FilterExpression=Attr('order_id').eq(order_id),
                    Limit=1
                )
                items = response.get('Items', [])
                if not items:
                    print(f"Order not found: {order_id}")
                    return False
                created_at = items[0]['created_at']

            update_expr = "SET " + ", ".join(f"#{k} = :{k}" for k in updates.keys())
            expr_names = {f"#{k}": k for k in updates.keys()}
            expr_values = {f":{k}": v for k, v in updates.items()}

            self.table.update_item(
                Key={'order_id': order_id, 'created_at': created_at},
                UpdateExpression=update_expr,
                ExpressionAttributeNames=expr_names,
                ExpressionAttributeValues=expr_values
            )
            return True
        except Exception as ex:
            print(f"Order Update Error: {str(ex)}")
            return False

    def get_order(self, order_id: str, created_at: str = None) -> Optional[dict]:
        """依頼を取得

        Args:
            order_id: 依頼ID
            created_at: 作成日時（複合キーのため必要。省略時はscanで検索）
        """
        try:
            if created_at:
                response = self.table.get_item(Key={'order_id': order_id, 'created_at': created_at})
                return response.get('Item')
            else:
                # created_atがない場合はscanで検索
                response = self.table.scan(
                    FilterExpression=Attr('order_id').eq(order_id),
                    Limit=1
                )
                items = response.get('Items', [])
                return items[0] if items else None
        except Exception as ex:
            print(f"Order Get Error: {str(ex)}")
            return None

    def _detect_service_type(self, message: str) -> str:
        """サービス種別を検出"""
        if '切り抜き' in message:
            return 'cutout'
        elif '合成' in message:
            return 'composite'
        elif 'レタッチ' in message:
            return 'retouch'
        else:
            return 'other'

    def get_recent_order(self, group_id: Optional[str], user_id: str, minutes: int = 10) -> Optional[dict]:
        """直近の依頼を取得（指示書追加用）

        同じグループ/ユーザーからの直近N分以内の依頼があれば返す
        """
        try:
            cutoff_time = (datetime.now() - timedelta(minutes=minutes)).isoformat()

            # group_idがあればグループ単位、なければユーザー単位で検索
            search_id = group_id if group_id else user_id
            filter_attr = 'group_id' if group_id else 'customer_id'

            # スキャンで直近の依頼を検索（GSIがないため）
            response = self.table.scan(
                FilterExpression=Attr(filter_attr).eq(search_id) &
                                Attr('created_at').gte(cutoff_time) &
                                Attr('status').eq('received')
            )

            items = response.get('Items', [])
            if items:
                # 最新の依頼を返す
                items.sort(key=lambda x: x['created_at'], reverse=True)
                return items[0]

            return None

        except Exception as ex:
            print(f"Get recent order error: {str(ex)}")
            return None

    def add_attachment_to_order(self, order_id: str, attachment_info: str, created_at: str = None) -> bool:
        """依頼に添付ファイル情報を追加"""
        try:
            # 既存の添付ファイルリストを取得して追加
            order = self.get_order(order_id, created_at)
            if not order:
                return False

            created_at = order['created_at']
            attachments = order.get('attachments', [])
            attachments.append({
                'info': attachment_info,
                'added_at': datetime.now().isoformat()
            })

            self.table.update_item(
                Key={'order_id': order_id, 'created_at': created_at},
                UpdateExpression='SET attachments = :attachments',
                ExpressionAttributeValues={':attachments': attachments}
            )
            print(f"Added attachment to order: {order_id}")
            return True

        except Exception as ex:
            print(f"Add attachment error: {str(ex)}")
            return False

    @staticmethod
    def is_order_request(message: str) -> bool:
        """依頼メッセージかどうか判定"""
        keywords = ['お願い', 'レタッチ', '切り抜き', '加工', '依頼', '合成']
        return any(keyword in message for keyword in keywords)
