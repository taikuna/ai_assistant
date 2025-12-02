"""
メモサービス - AIへの注意事項を保存・読み込み
"""
from datetime import datetime
from typing import List, Optional

import boto3
from boto3.dynamodb.conditions import Key


class NotesService:
    """AIへの指示メモを管理するサービス"""

    def __init__(self, table_name: str = None):
        self.dynamodb = boto3.resource('dynamodb')
        self.table = self.dynamodb.Table(table_name or 'ai_secretary_notes')

    def add_note(self, content: str, note_type: str = "general") -> str:
        """メモを追加

        Args:
            content: メモの内容
            note_type: メモの種類（general, response_style, etc.）

        Returns:
            created_at: 作成日時
        """
        created_at = datetime.now().isoformat()

        item = {
            'note_type': note_type,
            'created_at': created_at,
            'content': content,
            'active': True
        }

        self.table.put_item(Item=item)
        print(f"Note added: {content[:50]}...")
        return created_at

    def get_all_notes(self, note_type: str = "general") -> List[dict]:
        """アクティブなメモを全て取得

        Args:
            note_type: メモの種類

        Returns:
            メモのリスト
        """
        try:
            response = self.table.query(
                KeyConditionExpression=Key('note_type').eq(note_type)
            )
            items = response.get('Items', [])
            # アクティブなものだけ
            return [item for item in items if item.get('active', True)]
        except Exception as ex:
            print(f"Get notes error: {str(ex)}")
            return []

    def get_notes_as_markdown(self, note_type: str = "general") -> str:
        """メモをマークダウン形式で取得（プロンプト用）

        Returns:
            マークダウン形式のメモ
        """
        notes = self.get_all_notes(note_type)

        if not notes:
            return ""

        lines = ["## 注意事項メモ", ""]
        for i, note in enumerate(notes, 1):
            lines.append(f"- {note['content']}")

        return "\n".join(lines)

    def delete_note(self, note_type: str, created_at: str) -> bool:
        """メモを削除（非アクティブに）

        Args:
            note_type: メモの種類
            created_at: 作成日時

        Returns:
            成功したかどうか
        """
        try:
            self.table.update_item(
                Key={'note_type': note_type, 'created_at': created_at},
                UpdateExpression='SET active = :active',
                ExpressionAttributeValues={':active': False}
            )
            return True
        except Exception as ex:
            print(f"Delete note error: {str(ex)}")
            return False

    def list_notes_formatted(self, note_type: str = "general") -> str:
        """メモ一覧をフォーマットして返す（確認用）

        Returns:
            フォーマットされたメモ一覧
        """
        notes = self.get_all_notes(note_type)

        if not notes:
            return "メモはありません。"

        lines = ["【登録済みメモ】", ""]
        for i, note in enumerate(notes, 1):
            lines.append(f"{i}. {note['content']}")
            lines.append(f"   (登録: {note['created_at'][:10]})")

        lines.append("")
        lines.append("削除する場合: メモ削除 [番号]")

        return "\n".join(lines)

    def delete_note_by_index(self, index: int, note_type: str = "general") -> bool:
        """番号でメモを削除

        Args:
            index: 1から始まるインデックス
            note_type: メモの種類

        Returns:
            成功したかどうか
        """
        notes = self.get_all_notes(note_type)

        if index < 1 or index > len(notes):
            return False

        note = notes[index - 1]
        return self.delete_note(note_type, note['created_at'])
