"""
パーサーユーティリティ - URL抽出、納期解析など
"""
import re
from datetime import datetime
from typing import List, Optional


def extract_urls(text: str) -> List[str]:
    """テキストからURLを抽出"""
    url_pattern = r'https?://[^\s<>"{}|\\^`\[\]]+'
    return re.findall(url_pattern, text)


def extract_deadline(text: str) -> Optional[str]:
    """テキストから納期を抽出"""
    from datetime import timedelta, timezone

    # 日本時間で処理
    jst = timezone(timedelta(hours=9))
    now = datetime.now(jst)
    year = now.year

    # 相対表現のパターン（本日、今日、明日など）
    relative_patterns = {
        r'本日|今日': 0,
        r'明日': 1,
        r'明後日|あさって': 2,
    }

    # 時間抽出パターン
    time_pattern = r'(\d{1,2})[時:](\d{0,2})'

    # 相対表現をチェック
    for pattern, days_offset in relative_patterns.items():
        if re.search(pattern, text):
            target_date = now + timedelta(days=days_offset)

            # 時間指定があるか確認
            time_match = re.search(time_pattern, text)
            if time_match:
                hour = int(time_match.group(1))
                minute = int(time_match.group(2)) if time_match.group(2) else 0
            else:
                # 時間指定なし: 18時、ただし本日で17時過ぎなら23:59
                if days_offset == 0 and now.hour >= 17:
                    hour, minute = 23, 59
                else:
                    hour, minute = 18, 0

            deadline_dt = datetime(target_date.year, target_date.month, target_date.day, hour, minute)
            return deadline_dt.strftime('%Y-%m-%d %H:%M')

    # 具体的な日付パターン
    date_patterns = [
        r'(\d{1,2})/(\d{1,2}).*?(\d{1,2}):(\d{2})',
        r'(\d{1,2})月(\d{1,2})日.*?(\d{1,2}):(\d{2})',
        r'(\d{1,2})/(\d{1,2}).*?(\d{1,2})時',
        r'(\d{1,2})月(\d{1,2})日.*?(\d{1,2})時',
        r'(\d{1,2})/(\d{1,2})',
        r'(\d{1,2})月(\d{1,2})日',
    ]

    for pattern in date_patterns:
        match = re.search(pattern, text)
        if match:
            groups = match.groups()
            month = int(groups[0])
            day = int(groups[1])
            hour = int(groups[2]) if len(groups) > 2 else None
            minute = int(groups[3]) if len(groups) > 3 else 0

            # 過去の月なら来年
            if month < now.month:
                year = now.year + 1

            # 時間指定なしの場合
            if hour is None:
                # 今日で17時過ぎなら23:59
                if month == now.month and day == now.day and now.hour >= 17:
                    hour, minute = 23, 59
                else:
                    hour = 18

            try:
                deadline_dt = datetime(year, month, day, hour, minute)
                return deadline_dt.strftime('%Y-%m-%d %H:%M')
            except ValueError:
                pass

    return None


def is_deadline_correction(text: str) -> bool:
    """納期修正のメッセージかどうか判定

    例: 「12月4日でした」「納期は12/5です」「12月3日に変更」
    """
    # 納期修正を示すキーワード
    correction_keywords = [
        r'でした',
        r'です$',
        r'に変更',
        r'変更で',
        r'修正',
        r'訂正',
        r'間違',
        r'納期',  # 「納期 12/4」のようなパターン
    ]

    # 日付パターン
    date_patterns = [
        r'\d{1,2}/\d{1,2}',
        r'\d{1,2}月\d{1,2}日',
    ]

    # 日付が含まれていて、かつ修正キーワードがあるか
    has_date = any(re.search(p, text) for p in date_patterns)
    has_correction = any(re.search(p, text) for p in correction_keywords)

    # 短いメッセージで日付のみの場合も納期修正とみなす（50文字以下）
    is_short_date_only = has_date and len(text.strip()) <= 50

    return has_date and (has_correction or is_short_date_only)


def extract_order_id_from_message(text: str) -> Optional[str]:
    """メッセージから案件ID（8文字の16進数）を抽出

    例: 「dd67008b 納期 12月4日」→ 「dd67008b」
    """
    # 8文字の16進数パターン
    match = re.search(r'\b([a-f0-9]{8})\b', text.lower())
    return match.group(1) if match else None
