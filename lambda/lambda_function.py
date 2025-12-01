"""
AI秘書 - メインエントリーポイント
LINE/Chatwork/Slack等からの依頼を受け付けるAIアシスタント

処理フロー:
1. メッセージ受信 → 即座にバックエンド処理（ダウンロード、DB保存等）
2. 1分後にpush messageで返信（取り消し対応）
3. 取り消しイベント受信 → 保留中の返信をキャンセル
"""
import json
import os

# ハンドラー
from handlers.line_handler import LineHandler
from handlers.base_handler import Attachment

# サービス
from services.ai_service import AIService
from services.drive_service import DriveService
from services.calendar_service import CalendarService
from services.notification_service import SlackNotificationService
from services.order_service import OrderService
from services.download_service import DownloadService, FileUploader, DownloadedFile
from services.delayed_response_service import DelayedResponseService, LinePushService
from services.greeting_service import GreetingService
from services.client_service import ClientService

# ユーティリティ
from utils.parsers import extract_urls, extract_deadline

# 設定
from config import SYSTEM_PROMPT, SUMMARY_PROMPT

# 遅延返信の有効/無効（環境変数で制御）
ENABLE_DELAYED_RESPONSE = os.environ.get('ENABLE_DELAYED_RESPONSE', 'false').lower() == 'true'
RESPONSE_DELAY_SECONDS = int(os.environ.get('RESPONSE_DELAY_SECONDS', '60'))


def lambda_handler(event, context):
    """Lambda メインハンドラー"""
    try:
        body = json.loads(event.get('body', '{}'))

        # unsend（取り消し）イベントの処理
        for e in body.get('events', []):
            if e.get('type') == 'unsend':
                handle_unsend_event(e)
                continue

        # プラットフォームを判定してハンドラーを選択
        handler = get_handler(event)
        if not handler:
            return response_ok()

        # サービスを初期化
        services = initialize_services()

        # メッセージを処理
        messages = handler.parse_event(event)

        for msg in messages:
            process_message(handler=handler, message=msg, **services)

    except Exception as ex:
        print(f"Error: {str(ex)}")
        import traceback
        print(traceback.format_exc())

    return response_ok()


def handle_unsend_event(event: dict):
    """メッセージ取り消しイベントを処理"""
    try:
        message_id = event.get('unsend', {}).get('messageId')
        if message_id:
            delayed_service = DelayedResponseService()
            delayed_service.cancel_response(message_id)
            print(f"Cancelled pending response for unsent message: {message_id}")
    except Exception as ex:
        print(f"Unsend handling error: {str(ex)}")


def initialize_services() -> dict:
    """サービスを初期化"""
    return {
        'ai_service': AIService(),
        'drive_service': DriveService(),
        'calendar_service': CalendarService(),
        'notification_service': SlackNotificationService(),
        'order_service': OrderService(),
        'download_service': DownloadService(),
        'file_uploader': FileUploader(),
        'delayed_service': DelayedResponseService() if ENABLE_DELAYED_RESPONSE else None,
        'push_service': LinePushService() if ENABLE_DELAYED_RESPONSE else None,
        'greeting_service': GreetingService(),
        'client_service': ClientService(),
    }


def get_handler(event):
    """イベントからプラットフォームを判定してハンドラーを返す"""
    body = event.get('body', '{}')

    if isinstance(body, str):
        try:
            parsed = json.loads(body)
            if 'events' in parsed:
                return LineHandler()
        except json.JSONDecodeError:
            pass

    return None


def process_message(
    handler,
    message,
    ai_service,
    drive_service,
    calendar_service,
    notification_service,
    order_service,
    download_service,
    file_uploader,
    delayed_service,
    push_service,
    greeting_service,
    client_service
):
    """メッセージを処理"""
    user_message = message.message_text
    user_name = message.user_name
    urls = extract_urls(user_message)
    message_id = message.raw_event.get('message', {}).get('id', '')
    group_id = message.group_id

    # クライアント情報を取得
    client = None
    company_folder_id = None
    is_registered = False

    if group_id:
        client = client_service.get_client_by_group_id(group_id)
    else:
        client = client_service.get_client_by_user_id(message.user_id)

    if client:
        is_registered = True
        company_name = client.company_name
        # 会社フォルダを取得または作成
        company_folder_id = client_service.get_or_create_company_folder(client)
    else:
        company_name = "未登録クライアント"
        print(f"Unregistered client - group_id: {group_id}, user_id: {message.user_id}")

    # 添付ファイルがある場合
    has_attachments = len(message.attachments) > 0

    if order_service.is_order_request(user_message) or has_attachments:
        # 依頼として処理
        order_id = order_service.save_order(
            user_id=message.user_id,
            user_name=user_name,
            message=user_message,
            group_id=group_id,
            urls=urls
        )

        # Google Driveにフォルダ作成（会社フォルダの下に）
        folder_url = None
        folder_id = None

        if urls or has_attachments:
            folder_result = drive_service.create_order_folder(
                order_id=order_id,
                customer_name=user_name,
                urls=urls,
                parent_folder_id=company_folder_id  # 会社フォルダがあればその下に
            )
            if folder_result:
                folder_url, folder_id = folder_result

        # URLからファイルをダウンロードしてアップロード
        if urls and folder_id:
            downloaded_files = download_service.download_all(urls)
            if downloaded_files:
                file_uploader.upload_files_to_folder(downloaded_files, folder_id)
                print(f"Uploaded {len(downloaded_files)} files from URLs")

        # LINE添付ファイルをダウンロードしてアップロード
        if has_attachments and folder_id:
            upload_line_attachments(handler, message.attachments, file_uploader, folder_id)

        # 納期を抽出
        deadline = extract_deadline(user_message)

        # カレンダーに登録
        if deadline:
            calendar_service.create_deadline_event(order_id, user_name, deadline, user_message)

        # AIでサマリーを作成してSlackに通知
        summary = ai_service.create_summary(user_message, SUMMARY_PROMPT)
        notification_service.send_order_notification(
            order_id=order_id,
            customer_name=user_name,
            summary=summary,
            deadline=deadline,
            folder_url=folder_url,
            company_name=company_name,
            group_id=group_id,
            is_registered=is_registered
        )

        # AIレスポンスを生成
        ai_response = ai_service.generate_response(user_message, SYSTEM_PROMPT, user_name)
        ai_response += f"\n\n(依頼ID: {order_id[:8]})"
        if folder_url:
            ai_response += f"\n完成データは以下のフォルダに保存されます\n📁 Drive: {folder_url}"
        if deadline:
            ai_response += f"\n📅 納期: {deadline}"

    else:
        # 通常の会話として処理
        ai_response = ai_service.generate_response(user_message, SYSTEM_PROMPT, user_name)

    # その日最初のやり取りなら挨拶を追加（会社名と相手の名前付き）
    print(f"Greeting check - company_name: {company_name}, user_name: {user_name}, group_id: {message.group_id}")
    ai_response = greeting_service.add_greeting_if_needed(
        response_text=ai_response,
        group_id=message.group_id,
        user_id=message.user_id,
        company_name=company_name,
        user_name=user_name
    )

    # 返信（遅延または即時）
    send_response(
        handler=handler,
        message=message,
        response_text=ai_response,
        message_id=message_id,
        delayed_service=delayed_service,
        push_service=push_service
    )


def upload_line_attachments(handler, attachments: list, file_uploader, folder_id: str):
    """LINE添付ファイルをダウンロードしてGoogle Driveにアップロード"""
    for attachment in attachments:
        try:
            result = handler.download_attachment(attachment)
            if result:
                content, content_type = result
                filename = handler.get_attachment_filename(attachment)

                downloaded_file = DownloadedFile(
                    filename=filename,
                    content=content,
                    content_type=content_type,
                    source_url=f"line://message/{attachment.content_id}"
                )
                file_uploader.upload_to_folder(downloaded_file, folder_id)
                print(f"Uploaded LINE attachment: {filename}")
        except Exception as ex:
            print(f"LINE attachment upload error: {str(ex)}")


def send_response(handler, message, response_text: str, message_id: str, delayed_service, push_service):
    """返信を送信（遅延または即時）"""
    if ENABLE_DELAYED_RESPONSE and delayed_service and push_service:
        # 遅延返信モード
        target_id = message.group_id if message.group_id else message.user_id
        delayed_service.queue_delayed_response(
            message_id=message_id,
            user_id=target_id,
            group_id=message.group_id,
            response_text=response_text,
            platform=message.platform,
            delay_seconds=RESPONSE_DELAY_SECONDS
        )
        print(f"Response queued for {RESPONSE_DELAY_SECONDS}s delay")
    else:
        # 即時返信モード
        handler.reply(message, response_text)


def response_ok():
    """正常レスポンスを返す"""
    return {
        'statusCode': 200,
        'headers': {'Content-Type': 'application/json'},
        'body': json.dumps({'message': 'OK'})
    }


# ===== 遅延返信処理用Lambda（別途設定が必要） =====

def delayed_response_handler(event, context):
    """SQSからの遅延返信処理用ハンドラー"""
    push_service = LinePushService()
    delayed_service = DelayedResponseService()

    for record in event.get('Records', []):
        try:
            body = json.loads(record['body'])
            message_id = body.get('message_id')

            # 保留中の返信を取得
            pending = delayed_service.get_pending_response(message_id)
            if not pending:
                print(f"No pending response for: {message_id} (cancelled or already sent)")
                continue

            # 返信を送信
            target_id = pending.get('group_id')
            if target_id == 'none':
                target_id = pending.get('user_id')

            if pending.get('group_id') and pending.get('group_id') != 'none':
                success = push_service.push_to_group(pending['group_id'], pending['response_text'])
            else:
                success = push_service.push_message(pending['user_id'], pending['response_text'])

            if success:
                delayed_service.mark_as_sent(message_id)

        except Exception as ex:
            print(f"Delayed response error: {str(ex)}")

    return {'statusCode': 200}
