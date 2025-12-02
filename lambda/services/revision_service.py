"""
修正履歴サービス - AIの返信修正を学習データとして保存
"""
import json
import uuid
from datetime import datetime
from typing import Optional, List

import boto3
from boto3.dynamodb.conditions import Key


class RevisionService:
    """修正履歴を管理するサービス"""

    def __init__(self, table_name: str = None):
        self.dynamodb = boto3.resource('dynamodb')
        self.table = self.dynamodb.Table(table_name or 'ai_secretary_revision_history')

    def save_revision(
        self,
        original_response: str,
        revision_instruction: str,
        revised_response: str,
        customer_message: str,
        customer_name: str = "",
        company_name: str = "",
        pending_id: str = ""
    ) -> str:
        """修正履歴を保存

        Args:
            original_response: 修正前のAI返信
            revision_instruction: 修正指示
            revised_response: 修正後のAI返信
            customer_message: お客様の元メッセージ（コンテキスト）
            customer_name: お客様名
            company_name: 会社名
            pending_id: 元の保留メッセージID

        Returns:
            revision_id: 修正履歴ID
        """
        revision_id = str(uuid.uuid4())[:12]
        now = datetime.now()
        created_at = now.isoformat()
        year_month = now.strftime('%Y-%m')  # 月次エクスポート用

        item = {
            'revision_id': revision_id,
            'created_at': created_at,
            'year_month': year_month,
            'original_response': original_response,
            'revision_instruction': revision_instruction,
            'revised_response': revised_response,
            'customer_message': customer_message[:1000],  # コンテキストとして保存
            'customer_name': customer_name,
            'company_name': company_name,
            'pending_id': pending_id,
        }

        self.table.put_item(Item=item)
        print(f"Revision history saved: {revision_id}")
        return revision_id

    def get_revisions_by_month(self, year_month: str) -> List[dict]:
        """月別の修正履歴を取得

        Args:
            year_month: 年月（例: "2025-12"）

        Returns:
            修正履歴のリスト
        """
        try:
            response = self.table.query(
                IndexName='year-month-index',
                KeyConditionExpression=Key('year_month').eq(year_month)
            )
            return response.get('Items', [])
        except Exception as ex:
            print(f"Get revisions error: {str(ex)}")
            return []

    def export_as_training_data(self, year_month: str) -> List[dict]:
        """学習データ形式でエクスポート

        Args:
            year_month: 年月（例: "2025-12"）

        Returns:
            QAペア形式のデータリスト
        """
        revisions = self.get_revisions_by_month(year_month)

        training_data = []
        for rev in revisions:
            # 入力: お客様メッセージ + 修正指示
            # 出力: 修正後の返信
            training_data.append({
                'input': {
                    'customer_message': rev.get('customer_message', ''),
                    'draft_response': rev.get('original_response', ''),
                    'revision_instruction': rev.get('revision_instruction', '')
                },
                'output': rev.get('revised_response', ''),
                'metadata': {
                    'revision_id': rev.get('revision_id', ''),
                    'created_at': rev.get('created_at', ''),
                    'company_name': rev.get('company_name', ''),
                }
            })

        return training_data

    def export_as_json(self, year_month: str) -> str:
        """JSON形式でエクスポート"""
        data = self.export_as_training_data(year_month)
        return json.dumps(data, ensure_ascii=False, indent=2)

    def export_as_csv(self, year_month: str) -> str:
        """CSV形式でエクスポート"""
        revisions = self.get_revisions_by_month(year_month)

        if not revisions:
            return ""

        # ヘッダー
        headers = [
            'revision_id',
            'created_at',
            'customer_message',
            'original_response',
            'revision_instruction',
            'revised_response',
            'company_name'
        ]

        lines = [','.join(headers)]

        for rev in revisions:
            row = [
                rev.get('revision_id', ''),
                rev.get('created_at', ''),
                self._escape_csv(rev.get('customer_message', '')),
                self._escape_csv(rev.get('original_response', '')),
                self._escape_csv(rev.get('revision_instruction', '')),
                self._escape_csv(rev.get('revised_response', '')),
                rev.get('company_name', '')
            ]
            lines.append(','.join(row))

        return '\n'.join(lines)

    def _escape_csv(self, value: str) -> str:
        """CSV用にエスケープ"""
        if not value:
            return '""'
        # ダブルクォートをエスケープし、全体をダブルクォートで囲む
        escaped = value.replace('"', '""').replace('\n', ' ').replace('\r', '')
        return f'"{escaped}"'

    def get_statistics(self, year_month: str = None) -> dict:
        """修正統計を取得

        Returns:
            統計情報
        """
        if year_month:
            revisions = self.get_revisions_by_month(year_month)
        else:
            # 全件取得（注意：大量データの場合はページネーション必要）
            response = self.table.scan()
            revisions = response.get('Items', [])

        if not revisions:
            return {'total': 0}

        # よくある修正指示のパターンを集計
        instructions = {}
        for rev in revisions:
            instruction = rev.get('revision_instruction', '')
            # 簡易的なキーワード抽出
            for keyword in ['丁寧', '敬語', '短く', '詳しく', '追加', '削除', '確認', '挨拶']:
                if keyword in instruction:
                    instructions[keyword] = instructions.get(keyword, 0) + 1

        return {
            'total': len(revisions),
            'period': year_month or 'all',
            'common_patterns': instructions
        }
