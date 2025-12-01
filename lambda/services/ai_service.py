"""
AI サービス - Gemini API との連携
"""
import json
import urllib.request
import os


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
