# analytics.py
"""
简单的埋点工具：
- 使用 gspread + service account 写入 Google Sheet
- 读取的是分字段的 Secrets：
  GOOGLE_SHEETS_PROJECT_ID
  GOOGLE_SHEETS_PRIVATE_KEY_ID
  GOOGLE_SHEETS_PRIVATE_KEY
  GOOGLE_SHEETS_CLIENT_EMAIL
  GOOGLE_SHEETS_CLIENT_ID
  GOOGLE_SHEETS_SHEET_ID
"""

import os
import json
from datetime import datetime

import gspread
from oauth2client.service_account import ServiceAccountCredentials

# ---------------------------------------------------------
# 1. 读取环境变量（对应 Streamlit Secrets）
# ---------------------------------------------------------

PROJECT_ID = os.getenv("GOOGLE_SHEETS_PROJECT_ID")
PRIVATE_KEY_ID = os.getenv("GOOGLE_SHEETS_PRIVATE_KEY_ID")
PRIVATE_KEY = os.getenv("GOOGLE_SHEETS_PRIVATE_KEY")
CLIENT_EMAIL = os.getenv("GOOGLE_SHEETS_CLIENT_EMAIL")
CLIENT_ID = os.getenv("GOOGLE_SHEETS_CLIENT_ID")
SHEET_ID = os.getenv("GOOGLE_SHEETS_SHEET_ID")

REQUIRED_VARS = [
    PROJECT_ID,
    PRIVATE_KEY_ID,
    PRIVATE_KEY,
    CLIENT_EMAIL,
    CLIENT_ID,
    SHEET_ID,
]

ANALYTICS_ENABLED = all(REQUIRED_VARS)

gc = None
worksheet = None

if ANALYTICS_ENABLED:
    try:
        # 注意：private_key 在 secrets 里是带 \n 的，需要还原成真正的换行
        fixed_private_key = PRIVATE_KEY.replace("\\n", "\n")

        credentials_dict = {
            "type": "service_account",
            "project_id": PROJECT_ID,
            "private_key_id": PRIVATE_KEY_ID,
            "private_key": fixed_private_key,
            "client_email": CLIENT_EMAIL,
            "client_id": CLIENT_ID,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
            "client_x509_cert_url": (
                "https://www.googleapis.com/robot/v1/metadata/x509/"
                + CLIENT_EMAIL.replace("@", "%40")
            ),
            "universe_domain": "googleapis.com",
        }

        scopes = [
            "https://spreadsheets.google.com/feeds",
            "https://www.googleapis.com/auth/drive",
        ]

        creds = ServiceAccountCredentials.from_json_keyfile_dict(
            credentials_dict, scopes=scopes
        )
        gc = gspread.authorize(creds)

        # 打开你的表
        sh = gc.open_by_key(SHEET_ID)
        worksheet = sh.sheet1

        # 如果是第一次使用，没有任何内容，则加上表头
        existing = worksheet.get_all_values()
        if not existing:
            worksheet.append_row(
                ["timestamp_utc", "event_type", "data_json"],
                value_input_option="USER_ENTERED",
            )

        print("[analytics] Google Sheet analytics 已启用。")

    except Exception as e:
        ANALYTICS_ENABLED = False
        print(f"[analytics] 初始化失败，已关闭埋点功能: {e}")
else:
    print("[analytics] 缺少必要的 GOOGLE_SHEETS_* 环境变量，已关闭埋点功能。")


# ---------------------------------------------------------
# 2. 对外接口：log_event
# ---------------------------------------------------------

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
        # 不抛出异常，避免影响主流程；错误可以在日志里查看
        print(f"[analytics] 写入事件失败: {e}")