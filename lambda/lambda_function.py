"""
LINE Bot - メインエントリーポイント
LINE/Chatwork/Slack等からの依頼を受け付けるアシスタント

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
from services.revision_service import RevisionService
from services.notes_service import NotesService
from services.unprocessed_message_service import UnprocessedMessageService
from services.user_mapping_service import UserMappingService

# ユーティリティ
from utils.parsers import extract_urls, extract_deadline, is_deadline_correction, extract_order_id_from_message

# 設定
from config import SYSTEM_PROMPT, SUMMARY_PROMPT

# 遅延返信の有効/無効（環境変数で制御）
ENABLE_DELAYED_RESPONSE = os.environ.get('ENABLE_DELAYED_RESPONSE', 'false').lower() == 'true'
RESPONSE_DELAY_SECONDS = int(os.environ.get('RESPONSE_DELAY_SECONDS', '60'))


def lambda_handler(event, context):
    """Lambda メインハンドラー"""
    try:
        # Slackインタラクション（ボタン押下等）の処理
        path = event.get('path', '')
        if path == '/slack':
            return handle_slack_interaction(event)

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


def handle_slack_interaction(event: dict):
    """Slackインタラクション（ボタン押下、モーダル送信）を処理"""
    import urllib.parse

    print(f"Slack interaction received - path: {event.get('path')}")
    print(f"Slack interaction body: {event.get('body', '')[:500]}")

    try:
        # Slackはpayloadをx-www-form-urlencodedで送信
        body = event.get('body', '')
        parsed = urllib.parse.parse_qs(body)
        payload = json.loads(parsed.get('payload', ['{}'])[0])
        print(f"Parsed payload type: {payload.get('type')}")

        interaction_type = payload.get('type')
        print(f"Slack interaction: {interaction_type}")

        # サービス初期化
        approval_service = ApprovalService()
        push_service = LinePushService()
        notification_service = SlackNotificationService()
        ai_service = AIService()

        if interaction_type == 'block_actions':
            # ボタン押下
            actions = payload.get('actions', [])
            if not actions:
                return response_ok()

            action = actions[0]
            action_id = action.get('action_id')
            pending_id = action.get('value')
            user_id = payload.get('user', {}).get('id')
            channel = payload.get('channel', {}).get('id')
            message_ts = payload.get('message', {}).get('ts')
            trigger_id = payload.get('trigger_id')

            print(f"Action: {action_id}, pending_id: {pending_id}")

            if action_id == 'approve_send':
                # 送信承認
                pending = approval_service.approve_message(pending_id)
                if pending:
                    mention_user_id = pending.get('mention_user_id') or None
                    if pending['target_type'] == 'group':
                        push_service.push_to_group(
                            pending['target_id'],
                            pending['response_text'],
                            mention_user_id=mention_user_id
                        )
                    else:
                        push_service.push_message(pending['target_id'], pending['response_text'])

                    # Slackメッセージを更新
                    notification_service.update_approval_message(
                        channel, message_ts, pending_id, 'approved', user_id
                    )
                    print(f"Approved and sent: {pending_id}")

            elif action_id == 'approve_reject':
                # 却下
                approval_service.reject_message(pending_id)
                notification_service.update_approval_message(
                    channel, message_ts, pending_id, 'rejected', user_id
                )
                print(f"Rejected: {pending_id}")

            elif action_id == 'approve_edit':
                # 修正モーダルを開く
                pending = approval_service.get_pending_message(pending_id)
                if pending:
                    notification_service.open_edit_modal(
                        trigger_id,
                        pending_id,
                        pending.get('response_text', '')
                    )

            elif action_id == 'view_full_message':
                # お客様メッセージ全文を表示
                pending = approval_service.get_pending_message(pending_id)
                if pending:
                    notification_service.open_full_message_modal(
                        trigger_id,
                        pending_id,
                        pending.get('customer_name', ''),
                        pending.get('original_message', '')
                    )

            elif action_id == 'create_delivery':
                # 納品メッセージを作成
                pending = approval_service.get_pending_message(pending_id)
                if not pending:
                    # 承認済みの場合も取得を試みる
                    pending = approval_service.get_message_by_id(pending_id)

                if pending:
                    customer_name = pending.get('customer_name', '')
                    company_name = pending.get('company_name', '')
                    target_id = pending.get('target_id', '')
                    target_type = pending.get('target_type', 'group')
                    mention_user_id = pending.get('mention_user_id', '')
                    response_text = pending.get('response_text', '')

                    # 元の返信から情報を抽出
                    import re
                    order_id_match = re.search(r'依頼ID:\s*([a-f0-9-]+)', response_text)
                    project_match = re.search(r'📋\s*案件名:\s*(.+)', response_text)
                    drive_match = re.search(r'📁\s*Drive:\s*(https://[^\s]+)', response_text)
                    deadline_match = re.search(r'📅\s*納期:\s*(.+)', response_text)

                    order_id = order_id_match.group(1) if order_id_match else ''
                    project_name = project_match.group(1).strip() if project_match else ''
                    drive_url = drive_match.group(1).strip() if drive_match else ''
                    deadline = deadline_match.group(1).strip() if deadline_match else ''

                    # 今日の日付を納品日として取得
                    from datetime import datetime, timezone, timedelta
                    jst = timezone(timedelta(hours=9))
                    today = datetime.now(jst).strftime('%Y年%m月%d日')

                    # 納品テンプレートメッセージを生成
                    delivery_message = f"""お待たせいたしました。
ご依頼いただいておりました作業が完了いたしましたので、納品いたします。

依頼ID: {order_id}
📋 案件名: {project_name}
📁 納品データ: {drive_url}
📅 納品日: {today}

ご確認のほど、よろしくお願いいたします。"""

                    # 新しい保留メッセージとして保存（納品用）
                    delivery_pending_id = approval_service.save_pending_message(
                        target_id=target_id,
                        target_type=target_type,
                        response_text=delivery_message,
                        customer_name=customer_name,
                        company_name=company_name,
                        original_message=f"【納品】{pending.get('original_message', '')[:200]}",
                        mention_user_id=mention_user_id
                    )

                    # 納品用の承認リクエストをSlackに送信
                    notification_service.send_delivery_approval_request(
                        pending_id=delivery_pending_id,
                        customer_name=customer_name,
                        company_name=company_name,
                        delivery_message=delivery_message,
                        has_mention=bool(mention_user_id)
                    )
                    print(f"Delivery approval request created: {delivery_pending_id}")

            elif action_id == 'unsend_message':
                # 送信取り消し → 再編集可能に
                pending = approval_service.get_message_by_id(pending_id)
                if pending:
                    # ステータスをpendingに戻す
                    approval_service.reopen_message(pending_id)

                    # 新しい承認リクエストを送信（元のメッセージ情報で）
                    notification_service.send_approval_request(
                        pending_id=pending_id,
                        customer_name=pending.get('customer_name', ''),
                        company_name=pending.get('company_name', ''),
                        original_message=pending.get('original_message', ''),
                        response_text=pending.get('response_text', ''),
                        has_mention=bool(pending.get('mention_user_id'))
                    )

                    # 元のメッセージを更新
                    notification_service.update_approval_message(
                        channel, message_ts, pending_id, 'reopened', user_id
                    )
                    print(f"Message unsent and reopened: {pending_id}")

        elif interaction_type == 'view_submission':
            # モーダル送信
            view = payload.get('view', {})
            callback_id = view.get('callback_id')

            if callback_id == 'edit_response_modal':
                pending_id = view.get('private_metadata')
                values = view.get('state', {}).get('values', {})

                # 修正後のテキストを取得（AI返信文部分のみ）
                new_text = values.get('response_block', {}).get('response_text', {}).get('value', '')
                prompt_instruction = values.get('prompt_block', {}).get('prompt_text', {}).get('value', '')

                # 元のメッセージ情報を取得
                pending = approval_service.get_pending_message(pending_id)
                if not pending:
                    pending = approval_service.get_message_by_id(pending_id)

                if pending:
                    # AIによる修正指示がある場合
                    if prompt_instruction:
                        # AIで再生成
                        revision_prompt = f"""以下の返信文を修正してください。

修正指示: {prompt_instruction}

現在の返信文:
{new_text}

修正後の返信文のみを出力してください。"""
                        new_text = ai_service.generate_response(revision_prompt, "あなたは文章を修正するアシスタントです。")

                    # 既存のメッセージの返信テキストを更新（新規作成しない）
                    approval_service.update_pending_response(pending_id, new_text)

                    # 同じIDで修正版の承認リクエストをSlackに送信
                    notification_service.send_approval_request(
                        pending_id=pending_id,  # 同じIDを使用
                        customer_name=pending.get('customer_name', ''),
                        company_name=pending.get('company_name', ''),
                        original_message=pending.get('original_message', ''),
                        response_text=new_text,
                        has_mention=bool(pending.get('mention_user_id'))
                    )
                    print(f"Edited message sent for re-approval (same ID): {pending_id}")

            # view_submissionには空のボディを返してモーダルを閉じる
            return {
                'statusCode': 200,
                'headers': {'Content-Type': 'application/json'},
                'body': ''
            }

        return response_ok()

    except Exception as ex:
        print(f"Slack interaction error: {str(ex)}")
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
        'revision_service': RevisionService(),  # 修正履歴（学習データ用）
        'notes_service': NotesService(),  # メモ機能
        'unprocessed_service': UnprocessedMessageService(),  # 未処理メッセージ管理
        'user_mapping_service': UserMappingService(),  # ユーザー名→ID マッピング
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
    ai_service=None,
    revision_service=None,
    notes_service=None,
    client_service=None
) -> bool:
    """承認グループからのコマンドを処理

    コマンド一覧:
    - 送信 ID: そのまま送信
    - 修正 ID: 自分で編集した内容で更新
    - プロンプト修正 ID：指示: AIに修正させる
    - メモ 内容: 注意事項を追加
    - メモ一覧: 登録済みメモを表示
    - メモ削除 番号: メモを削除
    - 登録キャンセル ID: 会社名登録をキャンセル

    Returns:
        True: コマンドが処理された
        False: コマンドではなかった
    """
    # カギ括弧などを除去してコマンドを認識しやすくする
    text = user_message.strip().lstrip('「『【').rstrip('」』】')

    # === 登録キャンセル ===
    if text.startswith('登録キャンセル ') or text.startswith('登録キャンセル　'):
        registration_id = text.split()[1] if len(text.split()) > 1 else None
        if registration_id and client_service:
            reg = approval_service.get_registration_for_cancel(registration_id)
            if reg:
                # クライアント登録を削除
                client_service.delete_client(reg['group_id'])
                approval_service.mark_registration_cancelled(registration_id)
                handler.reply(message, f"「{reg['company_name']}」の登録をキャンセルしました。")
            else:
                handler.reply(message, f"ID: {registration_id} が見つかりません。")
            return True

    # === メモ機能 ===

    # 「メモ一覧」- 登録済みメモを表示
    if text == 'メモ一覧':
        if notes_service:
            handler.reply(message, notes_service.list_notes_formatted())
        return True

    # 「メモ削除 番号」- メモを削除
    if text.startswith('メモ削除 ') or text.startswith('メモ削除　'):
        if notes_service:
            try:
                index = int(text.split()[1])
                if notes_service.delete_note_by_index(index):
                    handler.reply(message, f"メモ {index} を削除しました。")
                else:
                    handler.reply(message, f"メモ {index} が見つかりません。")
            except (ValueError, IndexError):
                handler.reply(message, "番号を指定してください。\n例: メモ削除 1")
        return True

    # 「メモ 内容」- メモを追加
    if text.startswith('メモ ') or text.startswith('メモ　'):
        if notes_service:
            content = text[3:].strip()
            if content:
                notes_service.add_note(content)
                handler.reply(message, f"メモを追加しました:\n{content}")
            else:
                handler.reply(message, "メモの内容を入力してください。\n例: メモ お世話になっておりますを必ず最初に入れる")
        return True

    # === 承認フロー ===

    # 「送信 ID」- そのまま送信
    if text.startswith('送信 ') or text.startswith('送信　'):
        pending_id = text.split()[1] if len(text.split()) > 1 else None
        if pending_id:
            pending = approval_service.approve_message(pending_id)
            if pending:
                # お客様に送信（グループの場合はメンション付き）
                mention_user_id = pending.get('mention_user_id') or None
                if pending['target_type'] == 'group':
                    success = push_service.push_to_group(
                        pending['target_id'],
                        pending['response_text'],
                        mention_user_id=mention_user_id
                    )
                else:
                    success = push_service.push_message(pending['target_id'], pending['response_text'])

                # 結果を確認グループに通知
                if success:
                    handler.reply(message, f"✅ 送信完了")
                else:
                    handler.reply(message, f"❌ 送信失敗")
            else:
                handler.reply(message, f"ID: {pending_id} が見つかりません。")
            return True

    # 「却下 ID」- 送信しない
    if text.startswith('却下 ') or text.startswith('却下　'):
        pending_id = text.split()[1] if len(text.split()) > 1 else None
        if pending_id:
            success = approval_service.reject_message(pending_id)
            if success:
                handler.reply(message, f"却下しました。")
            else:
                handler.reply(message, f"ID: {pending_id} が見つかりません。")
            return True

    # 「プロンプト修正 ID：指示内容」- AIに修正させて新しい案を表示
    if text.startswith('プロンプト修正 ') or text.startswith('プロンプト修正　'):
        parts = text[7:].strip()  # "プロンプト修正 "を除去
        if '：' in parts or ':' in parts:
            separator = '：' if '：' in parts else ':'
            id_part, instruction = parts.split(separator, 1)
            pending_id = id_part.strip()
            instruction = instruction.strip()
        else:
            pending_id = parts.split()[0] if parts.split() else None
            instruction = None

        if pending_id and instruction and ai_service:
            pending = approval_service.get_pending_message(pending_id)
            if pending:
                original_response = pending['response_text']

                # メモを取得してプロンプトに含める
                notes_text = ""
                if notes_service:
                    notes_text = notes_service.get_notes_as_markdown()

                # AIに修正を依頼
                revision_prompt = f"""以下の返信文を修正してください。

修正指示: {instruction}

{notes_text}

元の返信文:
{original_response}

修正後の返信文のみを出力してください。説明は不要です。"""

                revised_response = ai_service.generate_response(revision_prompt, "")

                # 保留メッセージを更新
                approval_service.update_pending_response(pending_id, revised_response)

                # 修正履歴を保存（学習データ用）
                if revision_service:
                    revision_service.save_revision(
                        original_response=original_response,
                        revision_instruction=instruction,
                        revised_response=revised_response,
                        customer_message=pending.get('original_message', ''),
                        customer_name=pending.get('customer_name', ''),
                        company_name=pending.get('company_name', ''),
                        pending_id=pending_id
                    )

                # 修正案をシンプルに表示
                revised_text = f"""{revised_response}

━━━━━━━━━━━━
送信 {pending_id}
修正 {pending_id}
プロンプト修正 {pending_id}："""
                handler.reply(message, revised_text)
            else:
                handler.reply(message, f"ID: {pending_id} が見つかりません。")
            return True
        elif pending_id and not instruction:
            handler.reply(message, "指示を入力してください。\n例: プロンプト修正 abc123：もっと丁寧に")
            return True

    # 「修正 ID」+改行+編集内容 - 自分で編集した内容で更新
    if text.startswith('修正 ') or text.startswith('修正　'):
        parts = text[3:].strip()  # "修正 "を除去
        # IDと改行後の内容を分離
        lines = parts.split('\n', 1)
        pending_id = lines[0].strip()
        edited_content = lines[1].strip() if len(lines) > 1 else None

        if pending_id and edited_content:
            pending = approval_service.get_pending_message(pending_id)
            if pending:
                # 保留メッセージを更新
                approval_service.update_pending_response(pending_id, edited_content)

                # 修正案をシンプルに表示
                revised_text = f"""{edited_content}

━━━━━━━━━━━━
送信 {pending_id}
修正 {pending_id}
プロンプト修正 {pending_id}："""
                handler.reply(message, revised_text)
            else:
                handler.reply(message, f"ID: {pending_id} が見つかりません。")
            return True
        elif pending_id and not edited_content:
            # 改行なしの場合は使い方を案内
            handler.reply(message, f"修正内容を改行後に入力してください。\n\n例:\n修正 {pending_id}\nお世話になっております。\n承知いたしました。")
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
    push_service_always,
    revision_service,
    notes_service,
    unprocessed_service,
    user_mapping_service
):
    """メッセージを処理"""
    user_message = message.message_text
    user_name = message.user_name
    message_id = message.raw_event.get('message', {}).get('id', '')
    group_id = message.group_id
    target_id = group_id if group_id else message.user_id
    admin_user_id = os.environ.get('ADMIN_USER_ID', '')
    is_proxy_send = False  # 代理送信フラグ
    proxy_user_id = None  # 代理送信時のメンション先ユーザーID
    has_trigger = False  # トリガーキーワードがあったかどうか（自動送信判定用）
    is_reply_mode = False  # 返信モード（AIへの返答。新規案件は作らない）

    # グループ発言時はユーザー名→IDのマッピングを保存（後で名前から検索できるように）
    if group_id and message.user_id and user_name:
        user_mapping_service.save_user_mapping(group_id, message.user_id, user_name)

    # 管理者コマンド: {company:会社名} でグループの会社名を登録/上書き（トリガー不要）
    # メッセージのどこにあっても検出可能
    if message.user_id == admin_user_id and group_id:
        import re
        company_match = re.search(r'\{company[：:](.+?)\}', user_message)
        if company_match:
            new_company_name = company_match.group(1).strip()

            # 既存の登録があるかチェック
            existing_client = client_service.get_client_by_group_id(group_id)
            old_company_name = existing_client.company_name if existing_client else None

            # 会社名を登録（新規または上書き）
            client_service.register_client(group_id, new_company_name)

            # 確認グループに通知（キャンセル用ID付き）
            import uuid
            registration_id = str(uuid.uuid4())[:8]

            # キャンセル用に登録情報を一時保存
            approval_service.save_registration_for_cancel(registration_id, group_id, new_company_name)

            if old_company_name:
                # 上書き更新
                notification_text = f"グループの会社名を「{old_company_name}」→「{new_company_name}」に変更しました。\n\n登録キャンセル {registration_id}"
                reply_text = f"会社名を「{old_company_name}」→「{new_company_name}」に変更しました。"
            else:
                # 新規登録
                notification_text = f"グループを「{new_company_name}」で登録しました。\n\n登録キャンセル {registration_id}"
                reply_text = f"会社名を「{new_company_name}」で登録しました。"

            push_service_always.push_to_group(
                approval_service.approval_group_id,
                notification_text
            )

            handler.reply(message, reply_text)
            return

    # グループの場合、トリガーキーワード(@ai, @AI, @依頼, ＠ai, ＠AI, ＠依頼)のチェック
    if group_id and not approval_service.is_approval_group(group_id):
        has_trigger = unprocessed_service.has_trigger_keyword(user_message)

        # AIが直近10分以内に返信していれば、返信待ち状態
        is_awaiting_reply, last_ai_response = greeting_service.is_awaiting_reply(group_id, message.user_id, minutes=10)

        if not has_trigger and not is_awaiting_reply:
            # トリガーがなく、返信待ちでもなければ未処理メッセージとして保存して終了
            unprocessed_service.save_unprocessed_message(
                group_id=group_id,
                message_id=message_id,
                user_id=message.user_id,
                user_name=user_name,
                message_text=user_message
            )
            print(f"Message saved as unprocessed (no trigger keyword): {message_id}")
            return
        elif is_awaiting_reply and not has_trigger:
            # 返信待ち状態でトリガーなし → AIに「自分宛てか」判定させる
            is_for_ai = ai_service.is_message_for_ai(user_message, last_ai_response)
            if not is_for_ai:
                # AI宛てではない → 未処理メッセージとして保存
                unprocessed_service.save_unprocessed_message(
                    group_id=group_id,
                    message_id=message_id,
                    user_id=message.user_id,
                    user_name=user_name,
                    message_text=user_message
                )
                print(f"Message not for AI, saved as unprocessed: {message_id}")
                return
            # AI宛て → 処理を続行（ただし新規案件は作らない）
            print(f"Processing as reply to AI (awaiting reply mode): {message_id}")
            is_reply_mode = True  # 返信モード（新規案件作成しない）
            has_trigger = True  # 自動送信時間帯の判定用にTrueにする
        elif has_trigger:
            # トリガーがある場合、未処理メッセージを取得して結合
            unprocessed_messages = unprocessed_service.get_unprocessed_messages(group_id)
            if unprocessed_messages:
                # 未処理メッセージとトリガーメッセージを結合
                combined_message = unprocessed_service.combine_messages(
                    unprocessed_messages,
                    trigger_message=user_message
                )
                user_message = combined_message
                print(f"Combined {len(unprocessed_messages)} unprocessed messages with trigger")
                # 未処理メッセージを削除
                unprocessed_service.delete_unprocessed_messages(group_id)
            else:
                # 未処理メッセージがない場合はトリガーキーワードを除去
                user_message = unprocessed_service.remove_trigger_keyword(user_message)

    # 管理者による代理送信: {sender:名前} で送信者名を上書き（メッセージ内のどこにあってもOK）
    if message.user_id == admin_user_id and not is_proxy_send:
        import re
        sender_match = re.search(r'\{sender[：:](.+?)\}', user_message)
        if sender_match:
            user_name = sender_match.group(1).strip()
            # {sender:名前} 部分を除去
            user_message = user_message[:sender_match.start()] + user_message[sender_match.end():]
            user_message = user_message.strip()
            is_proxy_send = True

            # 名前からユーザーIDを検索してメンション用に設定
            if group_id:
                found_user_id = user_mapping_service.get_user_id_by_name(group_id, user_name)
                if found_user_id:
                    proxy_user_id = found_user_id
                    print(f"Admin proxy send as: {user_name} (ID: {proxy_user_id})")
                else:
                    # 完全一致しなければ部分一致で検索
                    found_user = user_mapping_service.search_user_by_partial_name(group_id, user_name)
                    if found_user:
                        proxy_user_id = found_user.get('user_id')
                        print(f"Admin proxy send as: {user_name} -> {found_user.get('user_name')} (ID: {proxy_user_id})")
                    else:
                        print(f"Admin proxy send as: {user_name} (ID not found, no mention)")

    # URLを抽出（代理送信の場合は更新後のメッセージから）
    urls = extract_urls(user_message)

    # 承認グループからのメッセージは、コマンド処理のみ行う（通常の依頼処理はスキップ）
    if group_id and approval_service.is_approval_group(group_id):
        handle_approval_command(
            user_message=user_message,
            approval_service=approval_service,
            push_service=push_service_always,
            handler=handler,
            message=message,
            ai_service=ai_service,
            revision_service=revision_service,
            notes_service=notes_service,
            client_service=client_service
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

    # 納期修正の処理（「12月4日でした」のような短いメッセージ）
    if is_deadline_correction(user_message):
        new_deadline = extract_deadline(user_message)
        if new_deadline:
            # メッセージに案件IDが含まれているかチェック
            specified_order_id = extract_order_id_from_message(user_message)

            if specified_order_id:
                # 案件ID指定あり → その案件を直接更新
                # order_idは先頭8文字なので、部分一致で検索
                target_order = None
                recent_orders = order_service.get_recent_orders(group_id, message.user_id, minutes=1440)  # 24時間以内
                for order in recent_orders:
                    if order['order_id'].startswith(specified_order_id):
                        target_order = order
                        break

                if target_order:
                    order_id = target_order['order_id']
                    order_created_at = target_order['created_at']
                    old_deadline = target_order.get('deadline', '未設定')
                    project_name = target_order.get('project_name', '')

                    # 依頼の納期を更新
                    order_service.update_order(order_id, {'deadline': new_deadline}, order_created_at)

                    # カレンダーも更新
                    calendar_service.create_deadline_event(
                        order_id=order_id,
                        customer_name=user_name,
                        deadline=new_deadline,
                        description=f"納期修正: {old_deadline} → {new_deadline}"
                    )

                    if project_name:
                        ai_response = f"承知いたしました。\n「{project_name}」の納期を {new_deadline} に修正いたしました。"
                    else:
                        ai_response = f"承知いたしました。\n依頼（ID: {order_id[:8]}）の納期を {new_deadline} に修正いたしました。"

                    handler.reply(message, ai_response)
                    return
                else:
                    handler.reply(message, f"案件ID「{specified_order_id}」が見つかりませんでした。")
                    return

            # 直近の依頼を全て取得（60分以内）
            recent_orders = order_service.get_recent_orders(group_id, message.user_id, minutes=60)

            if len(recent_orders) == 0:
                # 該当なし
                pass  # 通常処理に進む
            elif len(recent_orders) == 1:
                # 1件のみ → そのまま更新
                recent_order = recent_orders[0]
                order_id = recent_order['order_id']
                order_created_at = recent_order['created_at']
                old_deadline = recent_order.get('deadline', '未設定')
                project_name = recent_order.get('project_name', '')

                # 依頼の納期を更新
                order_service.update_order(order_id, {'deadline': new_deadline}, order_created_at)

                # カレンダーも更新（新しいイベントを作成）
                calendar_service.create_deadline_event(
                    order_id=order_id,
                    customer_name=user_name,
                    deadline=new_deadline,
                    description=f"納期修正: {old_deadline} → {new_deadline}"
                )

                # 返信
                if project_name:
                    ai_response = f"承知いたしました。\n「{project_name}」の納期を {new_deadline} に修正いたしました。"
                else:
                    ai_response = f"承知いたしました。\n依頼（ID: {order_id[:8]}）の納期を {new_deadline} に修正いたしました。"

                handler.reply(message, ai_response)
                return
            else:
                # 2件以上 → どの案件か確認
                lines = ["どちらの案件の納期を修正しますか？", ""]
                for order in recent_orders:
                    oid = order['order_id'][:8]
                    pname = order.get('project_name', '（案件名なし）')
                    lines.append(f"• {oid}: {pname}")

                lines.append("")
                lines.append(f"案件IDを指定して再度お知らせください。")
                lines.append(f"例: 「{recent_orders[0]['order_id'][:8]} 納期 {new_deadline}」")

                handler.reply(message, "\n".join(lines))
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

    elif (is_order_request or has_attachments) and not is_reply_mode:
        # 新規依頼として処理（返信モードでは新規案件を作らない）

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

        # AIでサマリーを作成（承認後に通知するため保存）
        summary = ai_service.create_summary(user_message, SUMMARY_PROMPT)

        # メモを取得してシステムプロンプトに含める
        from datetime import datetime
        today_str = datetime.now().strftime('%Y年%m月%d日')
        notes_text = notes_service.get_notes_as_markdown() if notes_service else ""
        system_prompt_with_notes = SYSTEM_PROMPT.format(today=today_str)
        if notes_text:
            system_prompt_with_notes = f"{system_prompt_with_notes}\n\n{notes_text}"

        # AIレスポンスを生成
        ai_response = ai_service.generate_response(user_message, system_prompt_with_notes, user_name)
        ai_response += f"\n\n依頼ID: {order_id[:8]}"
        # 担当者名を追加
        ai_response += f"\n👤 {user_name}様"
        if project_name:
            ai_response += f"\n📋 案件名: {project_name}"
        if folder_url:
            ai_response += f"\n📁 Drive: {folder_url}"
        if deadline:
            ai_response += f"\n📅 納期: {deadline}"

    else:
        # 通常の会話として処理
        # メモを取得してシステムプロンプトに含める
        from datetime import datetime
        today_str = datetime.now().strftime('%Y年%m月%d日')
        notes_text = notes_service.get_notes_as_markdown() if notes_service else ""
        system_prompt_with_notes = SYSTEM_PROMPT.format(today=today_str)
        if notes_text:
            system_prompt_with_notes = f"{system_prompt_with_notes}\n\n{notes_text}"
        ai_response = ai_service.generate_response(user_message, system_prompt_with_notes, user_name)

    # その日最初のやり取りなら挨拶を追加（会社名と相手の名前付き）
    print(f"Greeting check - company_name: {company_name}, user_name: {user_name}, group_id: {message.group_id}")
    ai_response = greeting_service.add_greeting_if_needed(
        response_text=ai_response,
        group_id=message.group_id,
        user_id=message.user_id,
        company_name=company_name,
        user_name=user_name
    )

    # 依頼情報を収集（承認後に通知するため）
    order_info = None
    if 'order_id' in dir() and order_id:
        order_info = {
            'order_id': order_id,
            'summary': summary if 'summary' in dir() else '',
            'deadline': deadline if 'deadline' in dir() else None,
            'folder_url': folder_url if 'folder_url' in dir() else None,
            'is_registered': is_registered
        }

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
        original_message=user_message,
        mention_user_id=proxy_user_id if is_proxy_send else (message.user_id if group_id else None),  # 代理送信時は検索したID、通常時は発言者
        order_info=order_info,  # 依頼情報（承認後に通知するため）
        has_trigger=has_trigger  # トリガーキーワードがあれば自動送信時間帯は承認スキップ
    )

    # AIの返信内容を記録（返信待ち状態の判定用）
    greeting_service.record_contact(group_id, message.user_id, ai_response)


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
    original_message: str = "",
    mention_user_id: str = None,
    order_info: dict = None,
    has_trigger: bool = False
):
    """返信を送信（承認フロー、遅延、または即時）

    Args:
        has_trigger: トリガーキーワード(@ai等)があったかどうか
                     自動送信時間帯（日本時間4:00-13:00）でこれがTrueなら承認スキップ
    """

    # 承認フローが有効な場合
    if approval_service and approval_service.is_approval_enabled():
        # 自動送信時間帯（日本時間4:00-13:00）かつトリガーキーワードありなら承認スキップ
        if has_trigger and approval_service.is_auto_send_time():
            print(f"Auto-send time and has trigger keyword - skipping approval")
            # 即時送信（承認なし）
            handler.reply(message, response_text)
            return
        target_id = message.group_id if message.group_id else message.user_id
        target_type = 'group' if message.group_id else 'user'

        # 保留メッセージとして保存（メンション用ユーザーIDも保存）
        pending_id = approval_service.save_pending_message(
            target_id=target_id,
            target_type=target_type,
            response_text=response_text,
            customer_name=customer_name,
            company_name=company_name,
            original_message=original_message,
            mention_user_id=mention_user_id,
            order_info=order_info
        )

        # Slackにボタン付き承認リクエストを送信
        slack_service = SlackNotificationService()
        slack_service.send_approval_request(
            pending_id=pending_id,
            customer_name=customer_name,
            company_name=company_name,
            original_message=original_message,
            response_text=response_text,
            has_mention=bool(mention_user_id)  # メンションの有無を表示
        )

        print(f"Response sent to Slack for approval, pending_id: {pending_id}")
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
