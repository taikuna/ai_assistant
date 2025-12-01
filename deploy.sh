#!/bin/bash

# AI秘書 デプロイスクリプト
# 使い方: ./deploy.sh

set -e

echo "🚀 AI秘書をデプロイします..."

# 設定
FUNCTION_NAME="ai-secretary"
REGION="ap-northeast-1"

# プロジェクトルート
PROJECT_ROOT="$(cd "$(dirname "$0")" && pwd)"
LAMBDA_DIR="$PROJECT_ROOT/lambda"
BUILD_DIR="/tmp/ai_secretary_build"
ZIP_FILE="/tmp/ai_secretary_lambda.zip"

# ビルドディレクトリを作成
echo "📦 パッケージを作成中..."
rm -rf "$BUILD_DIR"
mkdir -p "$BUILD_DIR"

# Lambdaコードをコピー（ディレクトリ構造を維持）
cp "$LAMBDA_DIR/lambda_function.py" "$BUILD_DIR/"
cp "$LAMBDA_DIR/config.py" "$BUILD_DIR/"

# handlers/
mkdir -p "$BUILD_DIR/handlers"
cp "$LAMBDA_DIR/handlers/__init__.py" "$BUILD_DIR/handlers/"
cp "$LAMBDA_DIR/handlers/base_handler.py" "$BUILD_DIR/handlers/"
cp "$LAMBDA_DIR/handlers/line_handler.py" "$BUILD_DIR/handlers/"

# services/
mkdir -p "$BUILD_DIR/services"
cp "$LAMBDA_DIR/services/__init__.py" "$BUILD_DIR/services/"
cp "$LAMBDA_DIR/services/ai_service.py" "$BUILD_DIR/services/"
cp "$LAMBDA_DIR/services/drive_service.py" "$BUILD_DIR/services/"
cp "$LAMBDA_DIR/services/calendar_service.py" "$BUILD_DIR/services/"
cp "$LAMBDA_DIR/services/notification_service.py" "$BUILD_DIR/services/"
cp "$LAMBDA_DIR/services/order_service.py" "$BUILD_DIR/services/"
cp "$LAMBDA_DIR/services/download_service.py" "$BUILD_DIR/services/"
cp "$LAMBDA_DIR/services/delayed_response_service.py" "$BUILD_DIR/services/"
cp "$LAMBDA_DIR/services/greeting_service.py" "$BUILD_DIR/services/"
cp "$LAMBDA_DIR/services/client_service.py" "$BUILD_DIR/services/"
cp "$LAMBDA_DIR/services/queue_service.py" "$BUILD_DIR/services/"
cp "$LAMBDA_DIR/services/approval_service.py" "$BUILD_DIR/services/"

# クライアントマスターJSON
cp "$LAMBDA_DIR/clients.json" "$BUILD_DIR/"

# utils/
mkdir -p "$BUILD_DIR/utils"
cp "$LAMBDA_DIR/utils/__init__.py" "$BUILD_DIR/utils/"
cp "$LAMBDA_DIR/utils/parsers.py" "$BUILD_DIR/utils/"

# ZIPファイルを作成
cd "$BUILD_DIR"
zip -r "$ZIP_FILE" .

echo "📁 パッケージ内容:"
unzip -l "$ZIP_FILE"

# Lambda関数を更新
echo "☁️  Lambda関数を更新中..."
aws lambda update-function-code \
    --function-name "$FUNCTION_NAME" \
    --zip-file "fileb://$ZIP_FILE" \
    --region "$REGION"

# クリーンアップ
rm -rf "$BUILD_DIR"
rm -f "$ZIP_FILE"

echo "✅ デプロイ完了！"
echo ""
echo "📋 次のステップ:"
echo "   - LINEでテストメッセージを送信"
echo "   - Slackで通知を確認"
