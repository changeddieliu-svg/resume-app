import os
import json
from datetime import datetime
from typing import Optional, Dict

import streamlit as st
import gspread
from google.oauth2.service_account import Credentials

# ------------------ 基础配置 ------------------

SHEET_ID = os.getenv("GOOGLE_SHEETS_SHEET_ID") or os.getenv(
    "GOOGLE_SHEETS_SHEET_ID"
)

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]


def _build_service_account_info() -> Optional[Dict]:
    """从环境变量拼出 service account 的 info 字典."""
    project_id = os.getenv("GOOGLE_SHEETS_PROJECT_ID")
    private_key_id = os.getenv("GOOGLE_SHEETS_PRIVATE_KEY_ID")
    private_key = os.getenv("GOOGLE_SHEETS_PRIVATE_KEY")
    client_email = os.getenv("GOOGLE_SHEETS_CLIENT_EMAIL")
    client_id = os.getenv("GOOGLE_SHEETS_CLIENT_ID")

    if not all([project_id, private_key_id, private_key, client_email, client_id, SHEET_ID]):
        st.sidebar.warning(
            "⚠ Analytics 库未安装完整：缺少 Google Sheets 相关环境变量。\n\n"
            "请在 Secrets 中确认已配置：GOOGLE_SHEETS_*。"
        )
        return None

    # private_key 里的 \n 要转换成真正的换行
    private_key = private_key.replace("\\n", "\n")

    info = {
        "type": "service_account",
        "project_id": project_id,
        "private_key_id": private_key_id,
        "private_key": private_key,
        "client_email": client_email,
        "client_id": client_id,
        "token_uri": "https://oauth2.googleapis.com/token",
        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
        "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
        "client_x509_cert_url": f"https://www.googleapis.com/robot/v1/metadata/x509/"
                                f"{client_email.replace('@', '%40')}",
    }
    return info


@st.cache_resource(show_spinner=False)
def _get_sheet():
    """返回 Google Sheet 的第一个工作表，失败则返回 None。"""
    info = _build_service_account_info()
    if info is None:
        return None

    try:
        creds = Credentials.from_service_account_info(info, scopes=SCOPES)
        client = gspread.authorize(creds)
        sh = client.open_by_key(SHEET_ID)
        ws = sh.sheet1

        # 保证表头存在
        header = ws.row_values(1)
        expected = ["timestamp", "event", "session_id", "payload_json"]
        if header != expected:
            ws.clear()
            ws.append_row(expected)
        return ws
    except Exception as e:
        st.sidebar.warning(f"⚠ Analytics 已关闭：无法连接 Google Sheet（{e}）")
        return None


SHEET = _get_sheet()
ANALYTICS_ENABLED = SHEET is not None


def show_analytics_status():
    """在左侧显示当前 Analytics 状态."""
    if ANALYTICS_ENABLED:
        st.sidebar.info("📊 Analytics 已开启：Google Sheet 正在记录使用数据。")
    else:
        st.sidebar.warning("📊 Analytics 未开启：暂不记录使用数据。")


def _get_session_id() -> str:
    """为每个浏览器会话生成一个 session_id。"""
    if "session_id" not in st.session_state:
        import uuid

        st.session_state["session_id"] = str(uuid.uuid4())
    return st.session_state["session_id"]


def log_event(event: str, **payload):
    """记录一个事件到 Google Sheet。

    event: 事件类型，例如 'page_view', 'generate_click', 'generate_success', 'generate_error'
    payload: 额外信息，会被序列化为 JSON 放在 payload_json 字段
    """
    if not ANALYTICS_ENABLED:
        return

    ws = SHEET
    if ws is None:
        return

    try:
        session_id = _get_session_id()
        now = datetime.utcnow().isoformat(timespec="seconds") + "Z"

        row = [
            now,
            event,
            session_id,
            json.dumps(payload, ensure_ascii=False),
        ]
        ws.append_row(row)
    except Exception as e:
        # 不抛出到页面，只在 sidebar 提示一次即可
        st.sidebar.warning(f"⚠ 写入 Analytics 失败：{e}")