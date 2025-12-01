"""
依頼管理サービス - DynamoDB操作
"""
import uuid
import os
from datetime import datetime
from typing import List, Optional

import boto3


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
        urls: List[str]
    ) -> str:
        """依頼を保存"""
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
        }

        self.table.put_item(Item=item)
        print(f"Order saved: {order_id}")
        return order_id

    def update_order(self, order_id: str, updates: dict) -> bool:
        """依頼を更新"""
        try:
            update_expr = "SET " + ", ".join(f"#{k} = :{k}" for k in updates.keys())
            expr_names = {f"#{k}": k for k in updates.keys()}
            expr_values = {f":{k}": v for k, v in updates.items()}

            self.table.update_item(
                Key={'order_id': order_id},
                UpdateExpression=update_expr,
                ExpressionAttributeNames=expr_names,
                ExpressionAttributeValues=expr_values
            )
            return True
        except Exception as ex:
            print(f"Order Update Error: {str(ex)}")
            return False

    def get_order(self, order_id: str) -> Optional[dict]:
        """依頼を取得"""
        try:
            response = self.table.get_item(Key={'order_id': order_id})
            return response.get('Item')
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

    @staticmethod
    def is_order_request(message: str) -> bool:
        """依頼メッセージかどうか判定"""
        keywords = ['お願い', 'レタッチ', '切り抜き', '加工', '依頼', '合成']
        return any(keyword in message for keyword in keywords)
