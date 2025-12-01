"""
Google Calendar サービス
"""
import json
import os
from datetime import datetime, timedelta
from typing import Optional

from google.oauth2 import service_account
from googleapiclient.discovery import build


class CalendarService:
    """Google Calendar操作を行うサービス"""

    SCOPES = ['https://www.googleapis.com/auth/calendar']

    def __init__(self, service_account_info: dict = None, calendar_id: str = None):
        self.service_account_info = service_account_info or json.loads(
            os.environ.get('GOOGLE_SERVICE_ACCOUNT', '{}')
        )
        self.calendar_id = calendar_id or os.environ.get('GOOGLE_CALENDAR_ID')
        self._service = None

    @property
    def service(self):
        """Google Calendar APIサービスを取得（遅延初期化）"""
        if self._service is None:
            credentials = service_account.Credentials.from_service_account_info(
                self.service_account_info,
                scopes=self.SCOPES
            )
            self._service = build('calendar', 'v3', credentials=credentials)
        return self._service

    def create_deadline_event(
        self,
        order_id: str,
        customer_name: str,
        deadline: str,
        description: str = ""
    ) -> Optional[str]:
        """納期イベントを作成"""
        if not self.calendar_id:
            return None

        try:
            company = customer_name.split(" - ")[-1] if " - " in customer_name else customer_name
            deadline_dt = datetime.strptime(deadline, '%Y-%m-%d %H:%M')
            end_dt = deadline_dt + timedelta(hours=1)

            event = {
                'summary': f'【納期】{company} - {order_id[:8]}',
                'description': f'依頼者: {customer_name}\n\n{description[:500]}',
                'start': {
                    'dateTime': deadline_dt.isoformat(),
                    'timeZone': 'Asia/Tokyo',
                },
                'end': {
                    'dateTime': end_dt.isoformat(),
                    'timeZone': 'Asia/Tokyo',
                },
            }

            created_event = self.service.events().insert(
                calendarId=self.calendar_id,
                body=event
            ).execute()

            print(f"Calendar event created: {created_event.get('htmlLink')}")
            return created_event.get('htmlLink')

        except Exception as ex:
            print(f"Calendar Error: {str(ex)}")
            return None
