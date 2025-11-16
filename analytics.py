# analytics.py
"""
简单埋点：把事件写入 Google Sheet

依赖：
- streamlit
- gspread
- google-auth

读取的 Secrets（你现在已经配置好了）：
  GOOGLE_SHEETS_PROJECT_ID
  GOOGLE_SHEETS_PRIVATE_KEY_ID
  GOOGLE_SHEETS_PRIVATE_KEY   （多行，包含 BEGIN/END PRIVATE KEY）
  GOOGLE_SHEETS_CLIENT_EMAIL
  GOOGLE_SHEETS_CLIENT_ID
  GOOGLE_SHEETS_SHEET_ID
"""

from __future__ import annotations

import json
from datetime import datetime

import streamlit as st
import gspread
from google.oauth2.service_account import Credentials

# ------------- 读取 Secrets -------------

PROJECT_ID = st.secrets.get("GOOGLE_SHEETS_PROJECT_ID")
PRIVATE_KEY_ID = st.secrets.get("GOOGLE_SHEETS_PRIVATE_KEY_ID")
PRIVATE_KEY = st.secrets.get("GOOGLE_SHEETS_PRIVATE_KEY")
CLIENT_EMAIL = st.secrets.get("GOOGLE_SHEETS_CLIENT_EMAIL")
CLIENT_ID = st.secrets.get("GOOGLE_SHEETS_CLIENT_ID")
SHEET_ID = st.secrets.get("GOOGLE_SHEETS_SHEET_ID")

REQUIRED_VARS = {
    "PROJECT_ID": PROJECT_ID,
    "PRIVATE_KEY_ID": PRIVATE_KEY_ID,
    "PRIVATE_KEY": PRIVATE_KEY,
    "CLIENT_EMAIL": CLIENT_EMAIL,
    "CLIENT_ID": CLIENT_ID,
    "SHEET_ID": SHEET_ID,
}

ANALYTICS_ENABLED = all(REQUIRED_VARS.values())

gc = None
worksheet = None

if ANALYTICS_ENABLED:
    try:
        # 这里 PRIVATE_KEY 已经是多行的真实 key，**不要再做 .replace("\\n", "\n")**
        credentials_info = {
            "type": "service_account",
            "project_id": PROJECT_ID,
            "private_key_id": PRIVATE_KEY_ID,
            "private_key": PRIVATE_KEY,
            "client_email": CLIENT_EMAIL,
            "client_id": CLIENT_ID,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
            "client_x509_cert_url": (
                "https://www.googleapis.com/robot/v1/metadata/x509/"
                + CLIENT_EMAIL.replace("@", "%40")
            ),
        }

        scopes = [
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/drive",
        ]

        creds = Credentials.from_service_account_info(
            credentials_info,
            scopes=scopes,
        )
        gc = gspread.authorize(creds)

        sh = gc.open_by_key(SHEET_ID)
        worksheet = sh.sheet1

        existing = worksheet.get_all_values()
        if not existing:
            worksheet.append_row(
                ["timestamp_utc", "event_type", "data_json"],
                value_input_option="USER_ENTERED",
            )

        print("[analytics] ✅ Google Sheet analytics 已启用。")

    except Exception as e:
        ANALYTICS_ENABLED = False
        print(f"[analytics] ❌ 初始化失败，已关闭埋点功能: {e}")
else:
    missing = [k for k, v in REQUIRED_VARS.items() if not v]
    print(
        "[analytics] ⚠️ 缺少必要配置，已关闭埋点功能。缺失字段: "
        + ", ".join(missing)
    )


# ------------- 对外接口：log_event -------------

def log_event(event_type: str, data: dict):
    """
    记录一条事件到 Google Sheet。
    event_type: "page_view" / "generate" / "user_feedback" 等
    data: 任意可 JSON 序列化的字典
    """
    if not ANALYTICS_ENABLED or worksheet is None:
        return

    try:
        ts = datetime.utcnow().isoformat()
        data_json = json.dumps(data, ensure_ascii=False)
        row = [ts, event_type, data_json]
        worksheet.append_row(row, value_input_option="USER_ENTERED")
    except Exception as e:
        print(f"[analytics] 写入事件失败: {e}")