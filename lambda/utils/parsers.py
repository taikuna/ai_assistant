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
    patterns = [
        r'(\d{1,2})/(\d{1,2}).*?(\d{1,2}):(\d{2})',
        r'(\d{1,2})月(\d{1,2})日.*?(\d{1,2}):(\d{2})',
        r'(\d{1,2})/(\d{1,2}).*?(\d{1,2})時',
        r'(\d{1,2})月(\d{1,2})日.*?(\d{1,2})時',
        r'(\d{1,2})/(\d{1,2})',
        r'(\d{1,2})月(\d{1,2})日',
    ]

    now = datetime.now()
    year = now.year

    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            groups = match.groups()
            month = int(groups[0])
            day = int(groups[1])
            hour = int(groups[2]) if len(groups) > 2 else 18  # 時間指定なしは18時
            minute = int(groups[3]) if len(groups) > 3 else 0

            # 過去の月なら来年
            if month < now.month:
                year = now.year + 1

            try:
                deadline_dt = datetime(year, month, day, hour, minute)
                return deadline_dt.strftime('%Y-%m-%d %H:%M')
            except ValueError:
                pass

    return None
