"""
ベースハンドラー - 全てのメッセージングプラットフォーム用の抽象クラス
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional, List


@dataclass
class Attachment:
    """添付ファイル情報"""
    type: str  # 'image', 'video', 'audio', 'file'
    content_id: str  # プラットフォーム固有のID
    filename: Optional[str] = None
    content_type: Optional[str] = None


@dataclass
class IncomingMessage:
    """受信メッセージの統一フォーマット"""
    platform: str  # 'line', 'chatwork', 'slack'
    user_id: str
    user_name: str
    group_id: Optional[str]
    message_text: str
    reply_token: Optional[str]  # LINE用
    raw_event: dict  # 元のイベントデータ
    attachments: List[Attachment] = field(default_factory=list)
    mentioned_user_ids: List[str] = field(default_factory=list)  # メンションされたユーザーID


@dataclass
class OutgoingMessage:
    """送信メッセージの統一フォーマット"""
    text: str
    platform: str


class BaseHandler(ABC):
    """メッセージングプラットフォームの抽象ベースクラス"""

    @abstractmethod
    def parse_event(self, event: dict) -> List[IncomingMessage]:
        """イベントをパースして統一フォーマットに変換"""
        pass

    @abstractmethod
    def reply(self, message: IncomingMessage, response_text: str) -> bool:
        """メッセージに返信"""
        pass

    @abstractmethod
    def get_user_name(self, user_id: str, group_id: Optional[str] = None) -> str:
        """ユーザー名を取得"""
        pass

    def download_attachment(self, attachment: Attachment) -> Optional[bytes]:
        """添付ファイルをダウンロード（オプション実装）"""
        return None
