# analytics.py
"""
简单埋点工具：
- 使用 gspread + service account 写入 Google Sheet
- 从 Streamlit secrets / 环境变量中读取以下字段：

  GOOGLE_SHEETS_PROJECT_ID
  GOOGLE_SHEETS_PRIVATE_KEY_ID
  GOOGLE_SHEETS_PRIVATE_KEY   （可以是多行，也可以是一行带 \\n 的）
  GOOGLE_SHEETS_CLIENT_EMAIL
  GOOGLE_SHEETS_CLIENT_ID
  GOOGLE_SHEETS_SHEET_ID
"""

import os
import json
import traceback
from datetime import datetime

import gspread
from oauth2client.service_account import ServiceAccountCredentials

# 尝试导入 streamlit（本地调试可能没有）
try:
    import streamlit as st  # type: ignore
except Exception:
    st = None


def _get_secret(name: str) -> str | None:
    """
    优先取环境变量，其次取 st.secrets
    """
    val = os.getenv(name)
    if val:
        return val

    if st is not None:
        try:
            # st.secrets 是 dict-like
            return st.secrets.get(name)  # type: ignore[attr-defined]
        except Exception:
            return None
    return None


# ---------------------------------------------------------
# 1. 读取配置
# ---------------------------------------------------------

PROJECT_ID = _get_secret("GOOGLE_SHEETS_PROJECT_ID")
PRIVATE_KEY_ID = _get_secret("GOOGLE_SHEETS_PRIVATE_KEY_ID")
PRIVATE_KEY = _get_secret("GOOGLE_SHEETS_PRIVATE_KEY")
CLIENT_EMAIL = _get_secret("GOOGLE_SHEETS_CLIENT_EMAIL")
CLIENT_ID = _get_secret("GOOGLE_SHEETS_CLIENT_ID")
SHEET_ID = _get_secret("GOOGLE_SHEETS_SHEET_ID")

REQUIRED_VARS = {
    "PROJECT_ID": PROJECT_ID,
    "PRIVATE_KEY_ID": PRIVATE_KEY_ID,
    "PRIVATE_KEY": PRIVATE_KEY,
    "CLIENT_EMAIL": CLIENT_EMAIL,
    "CLIENT_ID": CLIENT_ID,
    "SHEET_ID": SHEET_ID,
}

ANALYTICS_ENABLED: bool = all(REQUIRED_VARS.values())
gc = None
worksheet = None

if ANALYTICS_ENABLED:
    try:
        # 兼容两种写法：
        # 1）Secrets 里是「一行 + \\n」   -> 需要 replace("\\n", "\n")
        # 2）Secrets 里是 """多行 BLOCK""" -> 已经是真实换行，不需要处理
        pk = PRIVATE_KEY or ""
        if "\\n" in pk:
            fixed_private_key = pk.replace("\\n", "\n")
        else:
            fixed_private_key = pk

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
                + (CLIENT_EMAIL or "").replace("@", "%40")
            ),
            "universe_domain": "googleapis.com",
        }

        # 用新版 scope（老的 feeds 也能用，但这个更标准）
        scopes = [
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/drive",
        ]

        creds = ServiceAccountCredentials.from_json_keyfile_dict(
            credentials_dict, scopes=scopes
        )
        gc = gspread.authorize(creds)

        # 打开表 & 默认用第一个 sheet
        sh = gc.open_by_key(SHEET_ID)  # type: ignore[arg-type]
        worksheet = sh.sheet1

        # 如果是第一次，没有任何内容，则写表头
        existing = worksheet.get_all_values()
        if not existing:
            worksheet.append_row(
                ["timestamp_utc", "event_type", "data_json"],
                value_input_option="USER_ENTERED",
            )

        print(
            f"[analytics] ✅ Google Sheet analytics 已启用，"
            f"project={PROJECT_ID}, sheet_id={SHEET_ID}"
        )

    except Exception as e:
        ANALYTICS_ENABLED = False
        print(f"[analytics] ❌ 初始化失败，已关闭埋点功能: {e}")
        traceback.print_exc()

else:
    missing = [k for k, v in REQUIRED_VARS.items() if not v]
    print(
        "[analytics] ⚠️ 缺少必要配置，已关闭埋点功能。缺失字段: "
        + ", ".join(missing)
    )


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
        # 初始化失败时直接返回，不打断主流程
        return

    try:
        ts = datetime.utcnow().isoformat()
        data_json = json.dumps(data, ensure_ascii=False)
        row = [ts, event_type, data_json]
        worksheet.append_row(row, value_input_option="USER_ENTERED")
        print(f"[analytics] ➕ 已写入事件: {event_type}")
    except Exception as e:
        # 只打印日志，不抛异常
        print(f"[analytics] ❌ 写入事件失败: {e}")
        traceback.print_exc()