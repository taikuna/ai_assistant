"""
AI サービス - Gemini API との連携
"""
import json
import urllib.request
import os
import base64
from typing import List, Tuple, Optional


class AIService:
    """AI応答を生成するサービス"""

    def __init__(self, api_key: str = None, model: str = "gemini-2.5-flash"):
        self.api_key = api_key or os.environ.get('GEMINI_API_KEY')
        self.model = model
        self.base_url = "https://generativelanguage.googleapis.com/v1beta/models"

    def generate_response(self, prompt: str, system_prompt: str = "", user_name: str = "") -> str:
        """AIレスポンスを生成"""
        url = f"{self.base_url}/{self.model}:generateContent?key={self.api_key}"

        full_prompt = system_prompt
        if user_name:
            full_prompt += f"\n\nお客様（{user_name}）: "
        full_prompt += prompt

        data = {
            "contents": [
                {"role": "user", "parts": [{"text": full_prompt}]}
            ]
        }

        headers = {"Content-Type": "application/json"}
        req = urllib.request.Request(url, json.dumps(data).encode(), headers)

        try:
            with urllib.request.urlopen(req, timeout=25) as res:
                result = json.loads(res.read().decode())
                return result['candidates'][0]['content']['parts'][0]['text']
        except Exception as ex:
            print(f"AI Error: {str(ex)}")
            return f"Error: {str(ex)}"

    def create_summary(self, message: str, summary_prompt: str) -> str:
        """メッセージのサマリーを作成"""
        url = f"{self.base_url}/{self.model}:generateContent?key={self.api_key}"

        data = {
            "contents": [
                {"role": "user", "parts": [{"text": summary_prompt.format(message=message)}]}
            ]
        }

        headers = {"Content-Type": "application/json"}
        req = urllib.request.Request(url, json.dumps(data).encode(), headers)

        try:
            with urllib.request.urlopen(req, timeout=15) as res:
                result = json.loads(res.read().decode())
                return result['candidates'][0]['content']['parts'][0]['text']
        except Exception as ex:
            print(f"Summary Error: {str(ex)}")
            return message[:200]

    def extract_project_name(self, message: str) -> str:
        """依頼メッセージから案件名を抽出"""
        url = f"{self.base_url}/{self.model}:generateContent?key={self.api_key}"

        prompt = f"""以下の依頼メッセージから、判別しやすい案件名を作成してください。

【作成ルール】
1. 同じ顧客から複数の依頼があっても区別できるようにする
2. 案件の特徴（サイズ、種類、枚数など）を含める
3. 「□案件」の後に書かれた内容があればベースにする
4. 短すぎず、判別に必要な情報を含める
5. 顧客名・担当者名は含めない（別で管理されるため）

例：
- 「A3 エモカット 2枚」
- 「商品カタログ B4 5ページ」
- 「ポートレート補正 3枚」

回答は案件名のみを出力してください。説明や記号は不要です。

依頼メッセージ:
{message}

案件名:"""

        data = {
            "contents": [
                {"role": "user", "parts": [{"text": prompt}]}
            ]
        }

        headers = {"Content-Type": "application/json"}
        req = urllib.request.Request(url, json.dumps(data).encode(), headers)

        try:
            with urllib.request.urlopen(req, timeout=10) as res:
                result = json.loads(res.read().decode())
                project_name = result['candidates'][0]['content']['parts'][0]['text'].strip()
                # 改行や余分な文字を削除
                project_name = project_name.split('\n')[0].strip()
                # 長すぎる場合は切り詰め
                if len(project_name) > 50:
                    project_name = project_name[:50]
                return project_name
        except Exception as ex:
            print(f"Project name extraction error: {str(ex)}")
            return ""

    def analyze_images(self, images: List[Tuple[bytes, str]], project_name: Optional[str] = None) -> str:
        """画像を解析して内容を説明

        Args:
            images: [(画像バイナリ, content_type), ...] のリスト
            project_name: 案件名（あれば確認に使用）

        Returns:
            画像の説明テキスト
        """
        url = f"{self.base_url}/{self.model}:generateContent?key={self.api_key}"

        # 画像パーツを作成
        parts = []

        # プロンプト（簡潔に、記号なし、断定的に）
        if project_name:
            prompt_text = f"""以下の画像を確認して、簡潔に報告してください。

ルール:
- 絵文字や記号（---、**など）は使わない
- 各画像は1行で簡潔に説明
- 最後に「{project_name}の案件に指示データとして追加登録します。」と断定的に伝える
- 全体で5行以内に収める"""
        else:
            prompt_text = """以下の画像を確認して、簡潔に報告してください。

ルール:
- 絵文字や記号（---、**など）は使わない
- 各画像は1行で簡潔に説明
- 最後に「指示データとして追加登録します。」と断定的に伝える
- 全体で5行以内に収める"""

        parts.append({"text": prompt_text})

        # 画像を追加（最大4枚）
        for i, (image_data, content_type) in enumerate(images[:4]):
            base64_image = base64.b64encode(image_data).decode('utf-8')
            parts.append({
                "inline_data": {
                    "mime_type": content_type,
                    "data": base64_image
                }
            })

        data = {
            "contents": [
                {"role": "user", "parts": parts}
            ]
        }

        headers = {"Content-Type": "application/json"}
        req = urllib.request.Request(url, json.dumps(data).encode(), headers)

        try:
            with urllib.request.urlopen(req, timeout=20) as res:
                result = json.loads(res.read().decode())
                return result['candidates'][0]['content']['parts'][0]['text']
        except Exception as ex:
            print(f"Image analysis error: {str(ex)}")
            return ""

    def is_message_for_ai(self, message: str, context: str = "") -> bool:
        """メッセージがAI宛てかどうかを判定

        Args:
            message: ユーザーのメッセージ
            context: 直前のAIの発言（あれば）

        Returns:
            True: AI宛ての返信
            False: 別の会話（AI宛てではない）
        """
        url = f"{self.base_url}/{self.model}:generateContent?key={self.api_key}"

        prompt = f"""あなたはグループチャットで動作するAIアシスタントです。
以下のメッセージが「AIへの返信・依頼」か「別の人同士の会話」かを判定してください。

【直前のAIの発言】
{context if context else "（なし）"}

【ユーザーのメッセージ】
{message}

【判定基準】
- AIが質問した内容への回答（納期、確認事項への返答など）→ AI宛て
- 依頼、お願い、質問 → AI宛て
- 日付や時間のみの返信（直前にAIが確認していれば）→ AI宛て
- 他のメンバーへの呼びかけ、雑談、業務連絡 → AI宛てではない
- 「了解」「ありがとう」など曖昧な場合、直前にAIが発言していればAI宛て

回答は「YES」または「NO」のみを出力してください。
YES = AI宛て
NO = AI宛てではない"""

        data = {
            "contents": [
                {"role": "user", "parts": [{"text": prompt}]}
            ]
        }

        headers = {"Content-Type": "application/json"}
        req = urllib.request.Request(url, json.dumps(data).encode(), headers)

        try:
            with urllib.request.urlopen(req, timeout=5) as res:
                result = json.loads(res.read().decode())
                answer = result['candidates'][0]['content']['parts'][0]['text'].strip().upper()
                is_for_ai = answer.startswith('YES')
                print(f"Message for AI check: '{message[:50]}...' -> {is_for_ai}")
                return is_for_ai
        except Exception as ex:
            print(f"Message check error: {str(ex)}")
            # エラー時はAI宛てと判定（安全側）
            return True

    def analyze_pdf(self, pdf_data: bytes, project_name: Optional[str] = None) -> str:
        """PDFを解析して内容を説明

        Args:
            pdf_data: PDFバイナリ
            project_name: 案件名（あれば確認に使用）

        Returns:
            PDFの説明テキスト
        """
        url = f"{self.base_url}/{self.model}:generateContent?key={self.api_key}"

        base64_pdf = base64.b64encode(pdf_data).decode('utf-8')

        if project_name:
            prompt_text = f"""以下のPDFを確認して、簡潔に報告してください。

ルール:
- 絵文字や記号（---、**など）は使わない
- 内容を2-3行で簡潔に要約
- 最後に「{project_name}の案件に指示データとして追加登録します。」と断定的に伝える
- 全体で5行以内に収める"""
        else:
            prompt_text = """以下のPDFを確認して、簡潔に報告してください。

ルール:
- 絵文字や記号（---、**など）は使わない
- 内容を2-3行で簡潔に要約
- 最後に「指示データとして追加登録します。」と断定的に伝える
- 全体で5行以内に収める"""

        parts = [
            {"text": prompt_text},
            {
                "inline_data": {
                    "mime_type": "application/pdf",
                    "data": base64_pdf
                }
            }
        ]

        data = {
            "contents": [
                {"role": "user", "parts": parts}
            ]
        }

        headers = {"Content-Type": "application/json"}
        req = urllib.request.Request(url, json.dumps(data).encode(), headers)

        try:
            with urllib.request.urlopen(req, timeout=25) as res:
                result = json.loads(res.read().decode())
                return result['candidates'][0]['content']['parts'][0]['text']
        except Exception as ex:
            print(f"PDF analysis error: {str(ex)}")
            return ""
