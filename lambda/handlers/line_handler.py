"""
LINE Messaging API ハンドラー
"""
import json
import urllib.request
import os
from typing import List, Optional, Tuple

from .base_handler import BaseHandler, IncomingMessage, Attachment


class LineHandler(BaseHandler):
    """LINE Messaging API用ハンドラー"""

    # LINEのメッセージタイプとAttachmentタイプのマッピング
    MESSAGE_TYPE_MAP = {
        'image': 'image',
        'video': 'video',
        'audio': 'audio',
        'file': 'file',
    }

    def __init__(self, channel_access_token: str = None):
        self.channel_access_token = channel_access_token or os.environ.get('LINE_CHANNEL_ACCESS_TOKEN')

    def parse_event(self, event: dict) -> List[IncomingMessage]:
        """LINEのWebhookイベントをパース"""
        messages = []
        body = json.loads(event.get('body', '{}'))

        for e in body.get('events', []):
            if e['type'] != 'message':
                continue

            user_id = e['source'].get('userId', 'unknown')
            group_id = e['source'].get('groupId')
            user_name = self.get_user_name(user_id, group_id)
            msg_type = e['message']['type']

            # テキストメッセージ
            if msg_type == 'text':
                msg = IncomingMessage(
                    platform='line',
                    user_id=user_id,
                    user_name=user_name,
                    group_id=group_id,
                    message_text=e['message']['text'],
                    reply_token=e['replyToken'],
                    raw_event=e,
                    attachments=[]
                )
                messages.append(msg)

            # 添付ファイル（画像、動画、音声、ファイル）
            elif msg_type in self.MESSAGE_TYPE_MAP:
                attachment = Attachment(
                    type=self.MESSAGE_TYPE_MAP[msg_type],
                    content_id=e['message']['id'],
                    filename=e['message'].get('fileName'),
                    content_type=self._get_content_type(msg_type)
                )

                msg = IncomingMessage(
                    platform='line',
                    user_id=user_id,
                    user_name=user_name,
                    group_id=group_id,
                    message_text=f"[{msg_type}ファイルが送信されました]",
                    reply_token=e['replyToken'],
                    raw_event=e,
                    attachments=[attachment]
                )
                messages.append(msg)

        return messages

    def _get_content_type(self, msg_type: str) -> str:
        """メッセージタイプからContent-Typeを推定"""
        type_map = {
            'image': 'image/jpeg',
            'video': 'video/mp4',
            'audio': 'audio/m4a',
            'file': 'application/octet-stream',
        }
        return type_map.get(msg_type, 'application/octet-stream')

    def reply(self, message: IncomingMessage, response_text: str) -> bool:
        """LINEに返信"""
        if not message.reply_token:
            return False

        url = 'https://api.line.me/v2/bot/message/reply'
        headers = {
            'Content-Type': 'application/json',
            'Authorization': f'Bearer {self.channel_access_token}'
        }
        data = {
            'replyToken': message.reply_token,
            'messages': [{'type': 'text', 'text': response_text[:5000]}]
        }

        try:
            req = urllib.request.Request(url, json.dumps(data).encode(), headers)
            urllib.request.urlopen(req)
            return True
        except Exception as ex:
            print(f"LINE Reply Error: {str(ex)}")
            return False

    def get_user_name(self, user_id: str, group_id: Optional[str] = None) -> str:
        """LINEユーザー名を取得"""
        try:
            if group_id:
                url = f'https://api.line.me/v2/bot/group/{group_id}/member/{user_id}'
            else:
                url = f'https://api.line.me/v2/bot/profile/{user_id}'

            headers = {'Authorization': f'Bearer {self.channel_access_token}'}
            req = urllib.request.Request(url, headers=headers)

            with urllib.request.urlopen(req) as res:
                profile = json.loads(res.read().decode())
                return profile.get('displayName', 'Unknown')
        except Exception:
            return 'Unknown'

    def download_attachment(self, attachment: Attachment) -> Optional[Tuple[bytes, str]]:
        """LINE添付ファイルをダウンロード

        Returns:
            Tuple[bytes, str]: (ファイル内容, Content-Type) or None
        """
        try:
            url = f'https://api-data.line.me/v2/bot/message/{attachment.content_id}/content'
            headers = {'Authorization': f'Bearer {self.channel_access_token}'}
            req = urllib.request.Request(url, headers=headers)

            with urllib.request.urlopen(req, timeout=60) as res:
                content = res.read()
                content_type = res.headers.get('Content-Type', attachment.content_type)
                return content, content_type

        except Exception as ex:
            print(f"LINE Attachment Download Error: {str(ex)}")
            return None

    def get_attachment_filename(self, attachment: Attachment) -> str:
        """添付ファイルのファイル名を生成"""
        if attachment.filename:
            return attachment.filename

        # ファイル名がない場合は生成
        extension_map = {
            'image': '.jpg',
            'video': '.mp4',
            'audio': '.m4a',
            'file': '',
        }
        ext = extension_map.get(attachment.type, '')
        return f"line_{attachment.content_id}{ext}"
