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
from services.queue_service import QueueService
from services.approval_service import ApprovalService

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
        'queue_service': QueueService(),
        'approval_service': ApprovalService(),
        'push_service_always': LinePushService(),  # 承認フロー用（常に有効）
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


def handle_approval_command(
    user_message: str,
    approval_service,
    push_service,
    handler,
    message,
    ai_service=None
) -> bool:
    """承認グループからのコマンドを処理

    Returns:
        True: コマンドが処理された
        False: コマンドではなかった
    """
    text = user_message.strip()

    # 「送信 ID」- そのまま送信
    if text.startswith('送信 ') or text.startswith('送信　'):
        pending_id = text.split()[1] if len(text.split()) > 1 else None
        if pending_id:
            pending = approval_service.approve_message(pending_id)
            if pending:
                # お客様に送信
                if pending['target_type'] == 'group':
                    success = push_service.push_to_group(pending['target_id'], pending['response_text'])
                else:
                    success = push_service.push_message(pending['target_id'], pending['response_text'])

                # 結果を確認グループに通知
                if success:
                    handler.reply(message, f"✅ 送信完了: {pending['customer_name']}（{pending['company_name']}）")
                else:
                    handler.reply(message, f"❌ 送信失敗: {pending['customer_name']}（{pending['company_name']}）")
            else:
                handler.reply(message, f"ID: {pending_id} の保留メッセージが見つかりません。")
            return True

    # 「却下 ID」- 送信しない
    if text.startswith('却下 ') or text.startswith('却下　'):
        pending_id = text.split()[1] if len(text.split()) > 1 else None
        if pending_id:
            success = approval_service.reject_message(pending_id)
            if success:
                handler.reply(message, f"ID: {pending_id} の返信を却下しました。")
            else:
                handler.reply(message, f"ID: {pending_id} の保留メッセージが見つかりません。")
            return True

    # 「修正 ID：指示内容」- AIに修正させて新しい案を表示
    if text.startswith('修正 ') or text.startswith('修正　'):
        # 「修正 abc123：ここを直して」形式をパース
        parts = text[3:].strip()  # "修正 "を除去
        if '：' in parts or ':' in parts:
            separator = '：' if '：' in parts else ':'
            id_part, instruction = parts.split(separator, 1)
            pending_id = id_part.strip()
            instruction = instruction.strip()
        else:
            # IDのみの場合
            pending_id = parts.split()[0] if parts.split() else None
            instruction = None

        if pending_id and instruction and ai_service:
            pending = approval_service.get_pending_message(pending_id)
            if pending:
                # AIに修正を依頼
                revision_prompt = f"""以下の返信文を修正してください。

修正指示: {instruction}

元の返信文:
{pending['response_text']}

修正後の返信文のみを出力してください。説明は不要です。"""

                revised_response = ai_service.generate_response(revision_prompt, "")

                # 保留メッセージを更新
                approval_service.update_pending_response(pending_id, revised_response)

                # 修正案をテキストで表示
                revised_text = f"""【修正案】ID: {pending_id}

■ 宛先
{pending['customer_name']}（{pending['company_name']}）

■ 修正指示
{instruction}

■ 修正後の返信案
{revised_response}

━━━━━━━━━━━━
「送信 {pending_id}」→ このまま送信
「修正 {pending_id}：指示内容」→ さらに修正"""
                handler.reply(message, revised_text)
            else:
                handler.reply(message, f"ID: {pending_id} の保留メッセージが見つかりません。")
            return True
        elif pending_id and not instruction:
            handler.reply(message, "修正指示を入力してください。\n例: 修正 abc123：もっと丁寧な表現にして")
            return True

    return False


def handle_company_registration(client_service, target_id: str, user_message: str, suggested_company: str) -> str:
    """会社名登録フローを処理

    Returns:
        返信メッセージ。Noneの場合は通常処理を続行
    """
    message_lower = user_message.strip().lower()

    # 「はい」「yes」などの肯定的な返答で、提案された会社名がある場合
    if suggested_company and message_lower in ['はい', 'yes', 'うん', 'ok', 'おk', 'そうです', 'それで']:
        # 提案された会社名で登録
        client_service.register_client(target_id, suggested_company)
        return f"会社名を「{suggested_company}」で登録しました。\n依頼をお待ちしております。"

    # 会社名が入力された場合
    if len(user_message.strip()) > 0 and len(user_message.strip()) <= 50:
        company_name = user_message.strip()

        # 既存の類似会社名を検索
        similar = client_service.find_similar_company(company_name)

        if similar and similar != company_name:
            # 類似の会社名がある場合は確認
            client_service.set_pending_registration(target_id, similar)
            return f"「{similar}」という会社が既に登録されています。\n同じ会社でよろしいですか？\n\n違う場合は「いいえ」と入力してください。"

        # 「いいえ」の場合は新規登録
        if message_lower in ['いいえ', 'no', 'ちがう', '違う', '違います']:
            client_service.set_pending_registration(target_id, '')
            return "正しい会社名を入力してください。"

        # 新規登録
        client_service.register_client(target_id, company_name)
        return f"会社名を「{company_name}」で登録しました。\n依頼をお待ちしております。"

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
    client_service,
    queue_service,
    approval_service,
    push_service_always
):
    """メッセージを処理"""
    user_message = message.message_text
    user_name = message.user_name
    urls = extract_urls(user_message)
    message_id = message.raw_event.get('message', {}).get('id', '')
    group_id = message.group_id
    target_id = group_id if group_id else message.user_id

    # 承認グループからのメッセージは、コマンド処理のみ行う（通常の依頼処理はスキップ）
    if group_id and approval_service.is_approval_group(group_id):
        handle_approval_command(
            user_message=user_message,
            approval_service=approval_service,
            push_service=push_service_always,
            handler=handler,
            message=message,
            ai_service=ai_service
        )
        return  # 承認グループからのメッセージは常にここで終了

    # クライアント情報を取得
    client = None
    company_folder_id = None
    is_registered = False

    if group_id:
        client = client_service.get_client_by_group_id(group_id)
    else:
        client = client_service.get_client_by_user_id(message.user_id)

    # 登録待ち状態かチェック
    is_pending, suggested_company = client_service.is_pending_registration(target_id)

    if is_pending:
        # 会社名登録フロー
        ai_response = handle_company_registration(
            client_service=client_service,
            target_id=target_id,
            user_message=user_message,
            suggested_company=suggested_company
        )
        if ai_response:
            # 登録処理の返信
            handler.reply(message, ai_response)
            return

    if client and client.company_name:
        is_registered = True
        company_name = client.company_name
        # 会社フォルダを取得または作成
        company_folder_id = client_service.get_or_create_company_folder(client)
    else:
        # 未登録の場合は登録フローを開始
        company_name = "未登録クライアント"
        print(f"Unregistered client - group_id: {group_id}, user_id: {message.user_id}")

        # 既存の会社名から予測
        similar_company = client_service.find_similar_company(user_name)

        # 登録待ち状態を設定
        client_service.set_pending_registration(target_id, similar_company)

        # 登録を促すメッセージ
        if similar_company:
            ai_response = f"このグループの会社名が未登録です。\n「{similar_company}」でよろしいですか？\n\n違う場合は正しい会社名を入力してください。"
        else:
            ai_response = "このグループの会社名が未登録です。\n会社名を入力してください。"

        handler.reply(message, ai_response)
        return

    # 添付ファイルがある場合
    has_attachments = len(message.attachments) > 0
    is_order_request = order_service.is_order_request(user_message)

    # 添付ファイルのみ（指示書の追加）かどうか判定
    # テキストが短い（20文字以下）か空で、添付ファイルがある場合のみ指示書追加として扱う
    is_attachment_only = has_attachments and len(user_message.strip()) <= 20 and not is_order_request

    # URLのみ（Dropbox等のリンク追加）かどうか判定
    # URLがあり、依頼キーワードがなく、テキストがURL以外に短い場合
    text_without_urls = user_message
    for url in urls:
        text_without_urls = text_without_urls.replace(url, '')
    is_url_only = len(urls) > 0 and len(text_without_urls.strip()) <= 20 and not is_order_request

    print(f"Message analysis - urls: {len(urls)}, has_attachments: {has_attachments}, is_order_request: {is_order_request}, is_attachment_only: {is_attachment_only}, is_url_only: {is_url_only}, text_without_urls: '{text_without_urls.strip()}'")

    # 直近の依頼を確認（指示書追加用）- 添付ファイルのみまたはURLのみの場合
    recent_order = None
    if is_attachment_only or is_url_only:
        recent_order = order_service.get_recent_order(group_id, message.user_id, minutes=30)
        print(f"Recent order search result: {recent_order}")

    if recent_order and is_attachment_only:
        # 直近の依頼に指示書を追加（非同期処理）
        order_id = recent_order['order_id']
        order_created_at = recent_order['created_at']
        folder_id = recent_order.get('drive_folder_id')
        project_name = recent_order.get('project_name', '')

        print(f"Adding attachment to recent order: {order_id}, project: {project_name}")

        # 先に返信を作成（ファイル処理は非同期で行う）
        if project_name:
            ai_response = f"データを受け取りました。\n{project_name}の案件に追加登録します。\n\n処理中...完了後にお知らせします。"
        else:
            ai_response = f"データを受け取りました。\n依頼（ID: {order_id[:8]}）に追加登録します。\n\n処理中...完了後にお知らせします。"

        # 添付ファイル情報をSQSにキュー（非同期処理）
        if has_attachments:
            attachments_data = [
                {
                    'content_id': att.content_id,
                    'filename': handler.get_attachment_filename(att),
                    'content_type': att.content_type
                }
                for att in message.attachments
            ]

            queue_service.queue_attachment_processing(
                order_id=order_id,
                order_created_at=order_created_at,
                folder_id=folder_id,
                project_name=project_name,
                attachments=attachments_data,
                target_id=target_id,
                is_group=bool(group_id),
                company_folder_id=company_folder_id,
                user_name=user_name
            )

            # 依頼に添付ファイル情報を追加
            attachment_info = f"{len(message.attachments)}件のファイル追加（処理中）"
            order_service.add_attachment_to_order(order_id, attachment_info, order_created_at)

    elif recent_order and is_url_only:
        # 直近の依頼にURLからダウンロードしたファイルを追加（非同期処理）
        order_id = recent_order['order_id']
        order_created_at = recent_order['created_at']
        folder_id = recent_order.get('drive_folder_id')
        project_name = recent_order.get('project_name', '')

        print(f"Adding URL files to recent order: {order_id}, project: {project_name}, urls: {urls}")

        # 先に返信を作成
        if project_name:
            ai_response = f"データリンクを受け取りました。\n{project_name}の案件に追加します。\n\nダウンロード処理中...完了後にお知らせします。"
        else:
            ai_response = f"データリンクを受け取りました。\n依頼（ID: {order_id[:8]}）に追加します。\n\nダウンロード処理中...完了後にお知らせします。"

        # URL処理をSQSにキュー（非同期処理）
        queue_service.queue_url_processing(
            order_id=order_id,
            order_created_at=order_created_at,
            folder_id=folder_id,
            project_name=project_name,
            urls=urls,
            target_id=target_id,
            is_group=bool(group_id)
        )

        # 依頼に追加情報を記録（URLも保存）
        url_info = f"{len(urls)}件のURL追加（処理中）"
        if text_without_urls.strip():
            url_info += f" - {text_without_urls.strip()}"
        url_info += f"\nURLs: {', '.join(urls)}"
        order_service.add_attachment_to_order(order_id, url_info, order_created_at)

    elif is_order_request or has_attachments:
        # 新規依頼として処理

        # 案件名を抽出
        project_name = ai_service.extract_project_name(user_message) if user_message.strip() else ""
        print(f"Extracted project name: {project_name}")

        order_id, order_created_at = order_service.save_order(
            user_id=message.user_id,
            user_name=user_name,
            message=user_message,
            group_id=group_id,
            urls=urls,
            project_name=project_name
        )

        # Google Driveにフォルダ作成（会社フォルダの下に）- 依頼なら常に作成
        folder_url = None
        folder_id = None

        folder_result = drive_service.create_order_folder(
            order_id=order_id,
            customer_name=user_name,
            urls=urls,
            parent_folder_id=company_folder_id,  # 会社フォルダがあればその下に
            project_name=project_name  # 案件名をフォルダ名に含める
        )
        if folder_result:
            folder_url, folder_id = folder_result
            # フォルダIDを依頼に保存
            order_service.update_order(order_id, {'drive_folder_id': folder_id}, order_created_at)

        # URL処理をSQSにキュー（非同期処理）
        if urls and folder_id:
            queue_service.queue_url_processing(
                order_id=order_id,
                order_created_at=order_created_at,
                folder_id=folder_id,
                project_name=project_name,
                urls=urls,
                target_id=target_id,
                is_group=bool(group_id)
            )
            print(f"URLs queued for processing: {urls}")

        # LINE添付ファイル処理をSQSにキュー（非同期処理）
        if has_attachments and folder_id:
            attachments_data = [
                {
                    'content_id': att.content_id,
                    'filename': handler.get_attachment_filename(att),
                    'content_type': att.content_type
                }
                for att in message.attachments
            ]

            queue_service.queue_attachment_processing(
                order_id=order_id,
                order_created_at=order_created_at,
                folder_id=folder_id,
                project_name=project_name,
                attachments=attachments_data,
                target_id=target_id,
                is_group=bool(group_id),
                company_folder_id=company_folder_id,
                user_name=user_name
            )
            print(f"Attachments queued for processing: {len(attachments_data)} files")

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
        if project_name:
            ai_response += f"\n📋 案件名: {project_name}"
        if folder_url:
            ai_response += f"\n📁 Drive: {folder_url}"
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

    # 返信（承認フロー、遅延、または即時）
    send_response(
        handler=handler,
        message=message,
        response_text=ai_response,
        message_id=message_id,
        delayed_service=delayed_service,
        push_service=push_service,
        approval_service=approval_service,
        customer_name=user_name,
        company_name=company_name,
        original_message=user_message
    )


def upload_line_attachments(handler, attachments: list, file_uploader, folder_id: str) -> int:
    """LINE添付ファイルをダウンロードしてGoogle Driveにアップロード

    Returns:
        アップロードしたファイル数
    """
    uploaded_count = 0
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
                uploaded_count += 1
        except Exception as ex:
            print(f"LINE attachment upload error: {str(ex)}")
    return uploaded_count


def send_response(
    handler,
    message,
    response_text: str,
    message_id: str,
    delayed_service,
    push_service,
    approval_service=None,
    customer_name: str = "",
    company_name: str = "",
    original_message: str = ""
):
    """返信を送信（承認フロー、遅延、または即時）"""

    # 承認フローが有効な場合
    if approval_service and approval_service.is_approval_enabled():
        target_id = message.group_id if message.group_id else message.user_id
        target_type = 'group' if message.group_id else 'user'

        # 保留メッセージとして保存
        pending_id = approval_service.save_pending_message(
            target_id=target_id,
            target_type=target_type,
            response_text=response_text,
            customer_name=customer_name,
            company_name=company_name,
            original_message=original_message
        )

        # 確認グループにテキストで送信
        approval_text = f"""【承認依頼】ID: {pending_id}

■ 宛先
{customer_name}（{company_name}）

■ お客様のメッセージ
{original_message[:300]}{"..." if len(original_message) > 300 else ""}

■ AIの返信案
{response_text}

━━━━━━━━━━━━
「送信 {pending_id}」→ このまま送信
「却下 {pending_id}」→ 送信しない
「修正 {pending_id}：指示内容」→ AIが修正"""

        # 確認グループにPush
        push_svc = LinePushService()
        push_svc.push_to_group(
            approval_service.approval_group_id,
            approval_text
        )

        print(f"Response sent to approval group, pending_id: {pending_id}")
        return

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
