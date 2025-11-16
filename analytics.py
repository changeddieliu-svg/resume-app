"""
analytics.py
把 Streamlit 里的埋点写到 Google Sheet 里。

依赖的 Secrets 结构（已经和你现在的一致）：

OPENAI_API_KEY = "..."
MODEL_NAME = "gpt-4o-mini"

GOOGLE_SHEETS_PROJECT_ID   = "resumeoptimizer-478323"
GOOGLE_SHEETS_PRIVATE_KEY_ID = "493b09c9e0f2adcc68c9d53e45ced474c1c7332c"
GOOGLE_SHEETS_PRIVATE_KEY = "-----BEGIN PRIVATE KEY-----\n...\n-----END PRIVATE KEY-----\n"
GOOGLE_SHEETS_CLIENT_EMAIL = "resume-analytics@resumeoptimizer-478323.iam.gserviceaccount.com"
GOOGLE_SHEETS_CLIENT_ID    = "115707362625148987470"
GOOGLE_SHEETS_SHEET_ID     = "1mC0SC1-DTLXvljjlPM2JOOQi8Jrq0dKTxTgWCD65764"
"""

import os
import json
import uuid
from datetime import datetime

import gspread
from google.oauth2.service_account import Credentials

# 尝试和 Streamlit 的 session 绑定一个稳定的 session_id
try:
    import streamlit as st

    _USE_STREAMLIT = True
except Exception:
    st = None
    _USE_STREAMLIT = False

# Google API 访问范围：只需要 Sheets 即可
_SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

# 本地兜底的 session_id（在没有 Streamlit 的环境下用）
_fallback_session_id = None


def _get_session_id() -> str:
    """返回一个在当前会话内稳定的 session_id"""
    global _fallback_session_id

    if _USE_STREAMLIT:
        if "sid" not in st.session_state:
            st.session_state["sid"] = str(uuid.uuid4())
        return st.session_state["sid"]
    else:
        if _fallback_session_id is None:
            _fallback_session_id = str(uuid.uuid4())
        return _fallback_session_id


def _build_credentials() -> Credentials:
    """
    从拆开的 environment variables 里拼出 service_account 信息，
    然后生成 google.oauth2.service_account.Credentials。
    """
    project_id = os.getenv("GOOGLE_SHEETS_PROJECT_ID")
    private_key_id = os.getenv("GOOGLE_SHEETS_PRIVATE_KEY_ID")
    private_key = os.getenv("GOOGLE_SHEETS_PRIVATE_KEY", "")
    client_email = os.getenv("GOOGLE_SHEETS_CLIENT_EMAIL")
    client_id = os.getenv("GOOGLE_SHEETS_CLIENT_ID")

    # 基本校验，避免静默失败
    if not all([project_id, private_key_id, private_key, client_email, client_id]):
        raise RuntimeError(
            "Google Sheets 环境变量缺失：请检查是否配置了 "
            "GOOGLE_SHEETS_PROJECT_ID / PRIVATE_KEY_ID / PRIVATE_KEY / "
            "CLIENT_EMAIL / CLIENT_ID"
        )

    # Streamlit Secrets 里常常需要写成带 \n 的字符串，这里统一替换为真实换行
    private_key = private_key.replace("\\n", "\n")

    service_account_info = {
        "type": "service_account",
        "project_id": project_id,
        "private_key_id": private_key_id,
        "private_key": private_key,
        "client_email": client_email,
        "client_id": client_id,
        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
        "token_uri": "https://oauth2.googleapis.com/token",
        "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
        "client_x509_cert_url": (
            "https://www.googleapis.com/robot/v1/metadata/x509/"
            + client_email.replace("@", "%40")
        ),
    }

    creds = Credentials.from_service_account_info(
        service_account_info, scopes=_SCOPES
    )
    return creds


def _get_worksheet():
    """
    拿到目标 Google Sheet 的第一个工作表（sheet1），
    并保证第 1 行是我们预期的表头。
    """
    sheet_id = os.getenv("GOOGLE_SHEETS_SHEET_ID")
    if not sheet_id:
        raise RuntimeError("GOOGLE_SHEETS_SHEET_ID 未配置。")

    creds = _build_credentials()
    gc = gspread.authorize(creds)
    sh = gc.open_by_key(sheet_id)
    ws = sh.sheet1

    # 如果 A1 不是我们预期的表头，就插入一行表头
    headers = ["timestamp", "event_type", "session_id", "data_json"]
    try:
        first_cell = ws.acell("A1").value
    except Exception:
        first_cell = None

    if first_cell != headers[0]:
        # 在最上面插入一行表头，把你之前那句“打开左上角日期选择器…”往下推一行
        ws.insert_row(headers, 1)

    return ws


def log_event(event_type: str, data: dict):
    """
    对外暴露的唯一接口：写一行日志到 Google Sheet。

    event_type: "page_view" / "generate" / "user_feedback" 等
    data:      任意可 JSON 序列化的 dict
    """
    ws = _get_worksheet()
    sid = _get_session_id()

    row = [
        datetime.utcnow().isoformat(),
        event_type,
        sid,
        json.dumps(data, ensure_ascii=False),
    ]

    # 使用 USER_ENTERED，让时间和数字在 Sheet 里显示得更自然
    ws.append_row(row, value_input_option="USER_ENTERED")