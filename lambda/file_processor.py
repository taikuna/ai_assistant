"""
ファイル処理ワーカー - SQSからメッセージを受け取って非同期でファイル処理
"""
import json
import os

from services.drive_service import DriveService
from services.download_service import DownloadService, FileUploader, DownloadedFile
from services.order_service import OrderService
from services.ai_service import AIService
from services.client_service import ClientService
from services.delayed_response_service import LinePushService
from services.approval_service import ApprovalService

# LINE添付ファイルダウンロード用
import urllib.request


def file_processor_handler(event, context):
    """SQSからのファイル処理リクエストを処理"""

    drive_service = DriveService()
    download_service = DownloadService()
    file_uploader = FileUploader()
    order_service = OrderService()
    ai_service = AIService()
    client_service = ClientService()
    push_service = LinePushService()
    approval_service = ApprovalService()

    for record in event.get('Records', []):
        try:
            body = json.loads(record['body'])
            process_file_task(
                body,
                drive_service=drive_service,
                download_service=download_service,
                file_uploader=file_uploader,
                order_service=order_service,
                ai_service=ai_service,
                client_service=client_service,
                push_service=push_service,
                approval_service=approval_service
            )
        except Exception as ex:
            print(f"File processor error: {str(ex)}")
            import traceback
            print(traceback.format_exc())

    return {'statusCode': 200}


def process_file_task(
    task: dict,
    drive_service,
    download_service,
    file_uploader,
    order_service,
    ai_service,
    client_service,
    push_service,
    approval_service
):
    """ファイル処理タスクを実行"""
    task_type = task.get('task_type')

    if task_type == 'process_attachments':
        process_attachments_task(
            task, drive_service, file_uploader, order_service,
            ai_service, client_service, push_service, approval_service
        )
    elif task_type == 'process_urls':
        process_urls_task(
            task, download_service, file_uploader, order_service, push_service, approval_service
        )
    else:
        print(f"Unknown task type: {task_type}")


def process_attachments_task(
    task: dict,
    drive_service,
    file_uploader,
    order_service,
    ai_service,
    client_service,
    push_service,
    approval_service
):
    """LINE添付ファイルを処理"""
    order_id = task.get('order_id')
    order_created_at = task.get('order_created_at')
    folder_id = task.get('folder_id')
    project_name = task.get('project_name', '')
    attachments = task.get('attachments', [])
    target_id = task.get('target_id')  # group_id or user_id
    is_group = task.get('is_group', False)
    company_folder_id = task.get('company_folder_id')
    user_name = task.get('user_name', '')

    channel_access_token = os.environ.get('LINE_CHANNEL_ACCESS_TOKEN')

    print(f"Processing attachments for order: {order_id}, attachments: {len(attachments)}")

    # フォルダがなければ作成
    if not folder_id and company_folder_id:
        folder_result = drive_service.create_order_folder(
            order_id=order_id,
            customer_name=user_name,
            urls=[],
            parent_folder_id=company_folder_id,
            project_name=project_name
        )
        if folder_result:
            folder_url, folder_id = folder_result
            order_service.update_order(order_id, {'drive_folder_id': folder_id}, order_created_at)
            print(f"Created folder: {folder_id}")

    if not folder_id:
        print("No folder_id available, skipping upload")
        return

    # 添付ファイルをダウンロードしてアップロード
    uploaded_files = []
    images_for_analysis = []
    pdf_for_analysis = None

    for att in attachments:
        content_id = att.get('content_id')
        filename = att.get('filename', f'file_{content_id}')
        content_type = att.get('content_type', 'application/octet-stream')

        try:
            # LINEからダウンロード
            url = f'https://api-data.line.me/v2/bot/message/{content_id}/content'
            headers = {'Authorization': f'Bearer {channel_access_token}'}
            req = urllib.request.Request(url, headers=headers)

            with urllib.request.urlopen(req, timeout=120) as res:
                content = res.read()
                actual_content_type = res.headers.get('Content-Type', content_type)

            file_size = len(content)
            print(f"Downloaded: {filename} ({file_size / 1024 / 1024:.1f}MB)")

            # Google Driveにアップロード
            downloaded_file = DownloadedFile(
                filename=filename,
                content=content,
                content_type=actual_content_type,
                source_url=f"line://message/{content_id}"
            )
            file_uploader.upload_to_folder(downloaded_file, folder_id)
            uploaded_files.append(filename)
            print(f"Uploaded: {filename}")

            # 解析用に保存（5MB以下の画像/PDFのみ）
            if file_size <= 5 * 1024 * 1024:
                if actual_content_type and actual_content_type.startswith('image/'):
                    images_for_analysis.append((content, actual_content_type))
                elif actual_content_type == 'application/pdf':
                    pdf_for_analysis = content

        except Exception as ex:
            print(f"Error processing attachment {content_id}: {str(ex)}")

    # 依頼に添付ファイル情報を追加
    if uploaded_files:
        attachment_info = f"{len(uploaded_files)}件のファイルをDriveに保存"
        order_service.add_attachment_to_order(order_id, attachment_info, order_created_at)

    # 画像/PDFを解析
    analysis_result = ""
    if images_for_analysis:
        analysis_result = ai_service.analyze_images(images_for_analysis, project_name)
    elif pdf_for_analysis:
        analysis_result = ai_service.analyze_pdf(pdf_for_analysis, project_name)

    # 処理完了を承認フローに送信
    if uploaded_files:
        # Driveフォルダの情報を取得
        order_info = order_service.get_order(order_id)
        folder_url = ""
        if order_info and order_info.get('drive_folder_id'):
            folder_url = f"https://drive.google.com/drive/folders/{order_info['drive_folder_id']}"

        # ファイル一覧を作成
        file_list = "\n".join([f"  ・{f}" for f in uploaded_files])

        # 通知メッセージを作成
        notification = f"""データを保存しました。

【保存ファイル】{len(uploaded_files)}件
{file_list}

📁 保存先: {folder_url}

ファイルをご確認ください。"""

        # 承認フローが有効なら承認グループに送信
        if approval_service.is_approval_enabled():
            target_type = 'group' if is_group else 'user'

            # 保留メッセージとして保存
            pending_id = approval_service.save_pending_message(
                target_id=target_id,
                target_type=target_type,
                response_text=notification,
                customer_name=user_name,
                company_name="",  # ファイル処理では会社名なし
                original_message=f"【ファイル保存完了】{len(uploaded_files)}件"
            )

            # 確認グループに送信
            approval_text = f"""【ファイル保存完了】
宛先: {user_name}

{notification}

━━━━━━━━━━━━
送信 {pending_id}
却下 {pending_id}"""

            push_service.push_to_group(approval_service.approval_group_id, approval_text)
            print(f"File notification sent to approval group, pending_id: {pending_id}")
        else:
            # 承認フローなしなら直接送信
            if is_group:
                push_service.push_to_group(target_id, notification)
            else:
                push_service.push_message(target_id, notification)

        print(f"File processing completed: {len(uploaded_files)} files for order {order_id}")


def process_urls_task(
    task: dict,
    download_service,
    file_uploader,
    order_service,
    push_service,
    approval_service
):
    """URLからファイルをダウンロードして処理"""
    order_id = task.get('order_id')
    order_created_at = task.get('order_created_at')
    folder_id = task.get('folder_id')
    project_name = task.get('project_name', '')
    urls = task.get('urls', [])
    target_id = task.get('target_id')
    is_group = task.get('is_group', False)
    user_name = task.get('user_name', '')

    print(f"Processing URLs for order: {order_id}, urls: {urls}")

    if not folder_id:
        print("No folder_id available, skipping URL download")
        return

    # URLからダウンロードしてアップロード
    downloaded_files = download_service.download_all(urls)

    if downloaded_files:
        file_uploader.upload_files_to_folder(downloaded_files, folder_id)

        # 依頼に情報を追加
        url_info = f"{len(downloaded_files)}件のファイルをURLからダウンロードしてDriveに保存"
        order_service.add_attachment_to_order(order_id, url_info, order_created_at)

        # Driveフォルダの情報を取得
        order_info = order_service.get_order(order_id)
        folder_url = ""
        if order_info and order_info.get('drive_folder_id'):
            folder_url = f"https://drive.google.com/drive/folders/{order_info['drive_folder_id']}"

        # ファイル一覧を作成
        file_list = "\n".join([f"  ・{f.filename}" for f in downloaded_files])

        # 通知メッセージを作成
        notification = f"""データをダウンロードして保存しました。

【保存ファイル】{len(downloaded_files)}件
{file_list}

📁 保存先: {folder_url}

ファイルをご確認ください。"""

        # 承認フローが有効なら承認グループに送信
        if approval_service.is_approval_enabled():
            target_type = 'group' if is_group else 'user'

            # 保留メッセージとして保存
            pending_id = approval_service.save_pending_message(
                target_id=target_id,
                target_type=target_type,
                response_text=notification,
                customer_name=user_name,
                company_name="",
                original_message=f"【URLダウンロード完了】{len(downloaded_files)}件"
            )

            # 確認グループに送信
            approval_text = f"""【URLダウンロード完了】
宛先: {user_name}

{notification}

━━━━━━━━━━━━
送信 {pending_id}
却下 {pending_id}"""

            push_service.push_to_group(approval_service.approval_group_id, approval_text)
            print(f"URL notification sent to approval group, pending_id: {pending_id}")
        else:
            # 承認フローなしなら直接送信
            if is_group:
                push_service.push_to_group(target_id, notification)
            else:
                push_service.push_message(target_id, notification)

        print(f"URL processing completed: {len(downloaded_files)} files for order {order_id}")
    else:
        print("No files downloaded from URLs")
