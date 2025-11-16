# analytics.py
"""
简单的 Google Sheets 埋点模块：
- 事件日志：events
- 用户反馈：feedback
- 错误：errors
- Token / 配额监控：quota（预留）

依赖：
- gspread
- oauth2client
"""

from __future__ import annotations

import json
import datetime as dt
from typing import Any, Dict, Optional

import streamlit as st

# 如果这些包没装，会在 UI 明确提示
try:
    import gspread
    from oauth2client.service_account import ServiceAccountCredentials
except Exception as e:  # noqa: BLE001
    gspread = None
    ServiceAccountCredentials = None
    st.sidebar.warning(f"⚠️ Analytics 库未安装完整：{e}. 请确认 requirements.txt 已包含 gspread 和 oauth2client。")


# ---------- 内部工具 ----------

def _get_session_id() -> str:
    """给每个浏览器会话一个稳定 id，方便区分用户行为。"""
    if "sid" not in st.session_state:
        st.session_state["sid"] = dt.datetime.utcnow().strftime("%Y%m%d%H%M%S%f")
    return st.session_state["sid"]


@st.cache_resource(show_spinner=False)
def _get_sheet_client():
    """初始化 Google Sheets 客户端和各个 worksheet。

    如果任何一步失败，会在侧边栏提示，并返回 (None, None, None, None)。
    """
    if gspread is None or ServiceAccountCredentials is None:
        return None, None, None, None

    try:
        # 1) 从 secrets 读取 service account JSON
        raw_json = st.secrets.get("GOOGLE_SERVICE_ACCOUNT_JSON")
        if not raw_json:
            st.sidebar.warning("⚠️ 未配置 GOOGLE_SERVICE_ACCOUNT_JSON，Analytics 已关闭。")
            return None, None, None, None

        # raw_json 是字符串，需要转成 dict
        try:
            svc_info = json.loads(raw_json)
        except json.JSONDecodeError as e:  # noqa: F841
            st.sidebar.warning("⚠️ GOOGLE_SERVICE_ACCOUNT_JSON 解析失败，请检查是否完整复制。")
            return None, None, None, None

        # 2) 创建凭证 & client
        scope = [
            "https://spreadsheets.google.com/feeds",
            "https://www.googleapis.com/auth/drive",
        ]
        creds = ServiceAccountCredentials.from_json_keyfile_dict(svc_info, scope)
        client = gspread.authorize(creds)

        # 3) 打开指定的 Sheet
        sheet_id = st.secrets.get("GOOGLE_SHEET_ID")
        if not sheet_id:
            st.sidebar.warning("⚠️ 未配置 GOOGLE_SHEET_ID，Analytics 已关闭。")
            return None, None, None, None

        sh = client.open_by_key(sheet_id)

        # 4) 准备各个 worksheet（没有就创建）
        def get_or_create_ws(title: str, headers: list[str]):
            try:
                ws = sh.worksheet(title)
            except gspread.WorksheetNotFound:
                ws = sh.add_worksheet(title=title, rows=2000, cols=len(headers) + 2)
                ws.append_row(headers, value_input_option="USER_ENTERED")
                return ws

            # 如果第一行不是我们想要的表头，就补上（不会删除你已有的数据）
            try:
                first_row = ws.row_values(1)
            except Exception:
                first_row = []

            if not first_row:
                ws.append_row(headers, value_input_option="USER_ENTERED")
            return ws

        events_ws = get_or_create_ws(
            "events",
            ["timestamp_utc", "session_id", "event_type", "user_email", "extra_json"],
        )
        feedback_ws = get_or_create_ws(
            "feedback",
            ["timestamp_utc", "session_id", "rating", "comment", "contact"],
        )
        errors_ws = get_or_create_ws(
            "errors",
            ["timestamp_utc", "session_id", "error_message", "context_json"],
        )
        quota_ws = get_or_create_ws(
            "quota",
            ["timestamp_utc", "session_id", "event_type", "input_tokens", "output_tokens"],
        )

        return events_ws, feedback_ws, errors_ws, quota_ws

    except Exception as e:  # noqa: BLE001
        st.sidebar.warning(f"⚠️ 初始化 Google Sheets 失败：{e}")
        return None, None, None, None


# ---------- 对外函数 ----------

def log_event(event_type: str, user_email: Optional[str] = None, extra: Optional[Dict[str, Any]] = None) -> None:
    """记录一般事件，例如 page_view / generation / download 等。"""
    events_ws, _, _, _ = _get_sheet_client()
    if events_ws is None:
        return

    try:
        ts = dt.datetime.utcnow().isoformat()
        sid = _get_session_id()
        payload = json.dumps(extra or {}, ensure_ascii=False)
        events_ws.append_row([ts, sid, event_type, user_email or "", payload], value_input_option="USER_ENTERED")
    except Exception as e:  # noqa: BLE001
        st.sidebar.warning(f"⚠️ 写入事件日志失败：{e}")


def log_feedback(rating: int, comment: str = "", contact: str = "") -> None:
    """记录用户反馈（你可以在产品里加一个简单的反馈框再调用）。"""
    _, feedback_ws, _, _ = _get_sheet_client()
    if feedback_ws is None:
        return

    try:
        ts = dt.datetime.utcnow().isoformat()
        sid = _get_session_id()
        feedback_ws.append_row([ts, sid, rating, comment, contact], value_input_option="USER_ENTERED")
    except Exception as e:  # noqa: BLE001
        st.sidebar.warning(f"⚠️ 写入反馈失败：{e}")


def log_error(error_message: str, context: Optional[Dict[str, Any]] = None) -> None:
    """记录错误信息，方便排查用户问题。"""
    _, _, errors_ws, _ = _get_sheet_client()
    if errors_ws is None:
        return

    try:
        ts = dt.datetime.utcnow().isoformat()
        sid = _get_session_id()
        context_json = json.dumps(context or {}, ensure_ascii=False)
        errors_ws.append_row([ts, sid, error_message, context_json], value_input_option="USER_ENTERED")
    except Exception:
        # 这里就不再重复提示了，避免报错时疯狂刷 warning
        pass


def log_quota(event_type: str, input_tokens: int = 0, output_tokens: int = 0) -> None:
    """可选：记录一次请求大概用了多少 token，帮助你监控开销。"""
    _, _, _, quota_ws = _get_sheet_client()
    if quota_ws is None:
        return

    try:
        ts = dt.datetime.utcnow().isoformat()
        sid = _get_session_id()
        quota_ws.append_row(
            [ts, sid, event_type, int(input_tokens or 0), int(output_tokens or 0)],
            value_input_option="USER_ENTERED",
        )
    except Exception:
        pass