# analytics.py
# 使用你在 secrets 中配置的 GOOGLE_SHEETS_* 写入同一张 Google Sheet

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Dict, Optional

import streamlit as st

# 这两个是 requirements.txt 里已经装好的
try:
    import gspread
    from google.oauth2.service_account import Credentials

    _HAS_SHEETS = True
except Exception:
    _HAS_SHEETS = False


def _build_service_account_info() -> Optional[dict]:
    """
    根据你现在的 secrets 结构，拼出一个 service_account_info dict，
    用于 Credentials.from_service_account_info(...)
    """
    required_keys = [
        "GOOGLE_SHEETS_PROJECT_ID",
        "GOOGLE_SHEETS_PRIVATE_KEY_ID",
        "GOOGLE_SHEETS_PRIVATE_KEY",
        "GOOGLE_SHEETS_CLIENT_EMAIL",
        "GOOGLE_SHEETS_CLIENT_ID",
    ]
    for key in required_keys:
        if key not in st.secrets:
            return None

    return {
        "type": "service_account",
        "project_id": st.secrets["GOOGLE_SHEETS_PROJECT_ID"],
        "private_key_id": st.secrets["GOOGLE_SHEETS_PRIVATE_KEY_ID"],
        "private_key": st.secrets["GOOGLE_SHEETS_PRIVATE_KEY"],
        "client_email": st.secrets["GOOGLE_SHEETS_CLIENT_EMAIL"],
        "client_id": st.secrets["GOOGLE_SHEETS_CLIENT_ID"],
        # 这两个是固定写死的标准地址
        "token_uri": "https://oauth2.googleapis.com/token",
    }


def _get_worksheet():
    """
    返回 Google Sheet 的第一个工作表（Sheet1）。
    配置不完整 / 依赖缺失时返回 None，不抛错。
    """
    if not _HAS_SHEETS:
        return None

    if "GOOGLE_SHEETS_SHEET_ID" not in st.secrets:
        return None

    service_info = _build_service_account_info()
    if service_info is None:
        return None

    try:
        creds = Credentials.from_service_account_info(
            service_info,
            scopes=["https://www.googleapis.com/auth/spreadsheets"],
        )
        client = gspread.authorize(creds)
        sheet_id = st.secrets["GOOGLE_SHEETS_SHEET_ID"]
        sh = client.open_by_key(sheet_id)
        ws = sh.sheet1
        return ws
    except Exception as e:
        # 不要让异常影响主程序，只在日志里打印一下
        print("⚠ analytics: 无法连接 Google Sheet:", e)
        return None


def log_event(event_type: str, data: Dict[str, Any] | None = None) -> None:
    """
    供 app.py 调用的统一埋点入口。
    你在 app.py 里通过 safe_log_event(...) 调用的就是这个函数。
    每次调用会往 Google Sheet 追加一行：
    [时间戳, 事件类型, JSON 格式的 data]
    """
    ws = _get_worksheet()
    if ws is None:
        return

    try:
        ts = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
        payload = json.dumps(data or {}, ensure_ascii=False)
        ws.append_row([ts, event_type, payload], value_input_option="RAW")
    except Exception as e:
        print("⚠ analytics: 写入日志失败:", e)
        return