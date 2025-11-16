import datetime
import json
from typing import Any, Dict, Optional

import streamlit as st

# 尝试导入 Google 相关库
try:
    import gspread
    from google.oauth2 import service_account

    _ANALYTICS_LIB_OK = True
except Exception as e:  # noqa: BLE001
    _ANALYTICS_LIB_OK = False
    st.warning(
        f"⚠️ Analytics 库未安装完整：{e}。请确认 requirements.txt 已包含 gspread 和 google-auth。",
        icon="⚠️",
    )

# 全局缓存 worksheet，避免每次都连接
_WORKSHEET = None


def _load_service_account_info() -> Optional[Dict[str, Any]]:
    """
    从 secrets 中读取 Google Service Account 配置。

    支持两种方式：
    1）GOOGLE_SERVICE_ACCOUNT_JSON：完整 json（你现在用的这种）；
    2）拆分字段：GOOGLE_SHEETS_PROJECT_ID / PRIVATE_KEY_ID / PRIVATE_KEY / CLIENT_EMAIL / CLIENT_ID。
    """
    # 优先尝试完整 JSON（你现在就是这种）
    raw = st.secrets.get("GOOGLE_SERVICE_ACCOUNT_JSON", None)

    if raw is not None:
        try:
            # 如果是 dict（有些人用 TOML 的 [GOOGLE_SERVICE_ACCOUNT_JSON] 写法），直接用
            if isinstance(raw, dict):
                info = dict(raw)
            else:
                # 字符串 -> json
                info = json.loads(str(raw))
            return info
        except Exception as e:  # noqa: BLE001
            # 这里只警告，但不强制报错，我们还会尝试拆分字段的写法
            st.warning(
                f"⚠️ GOOGLE_SERVICE_ACCOUNT_JSON 解析失败：{e}。"
                "请确认 secrets 中 JSON 是否完整复制。",
                icon="⚠️",
            )

    # 兜底：尝试分段式（如果你以后想改回去，也没问题）
    keys = [
        "GOOGLE_SHEETS_PROJECT_ID",
        "GOOGLE_SHEETS_PRIVATE_KEY_ID",
        "GOOGLE_SHEETS_PRIVATE_KEY",
        "GOOGLE_SHEETS_CLIENT_EMAIL",
        "GOOGLE_SHEETS_CLIENT_ID",
    ]
    if not all(k in st.secrets for k in keys):
        # 两种方式都不满足，就不再继续了
        return None

    return {
        "type": "service_account",
        "project_id": st.secrets["GOOGLE_SHEETS_PROJECT_ID"],
        "private_key_id": st.secrets["GOOGLE_SHEETS_PRIVATE_KEY_ID"],
        "private_key": st.secrets["GOOGLE_SHEETS_PRIVATE_KEY"],
        "client_email": st.secrets["GOOGLE_SHEETS_CLIENT_EMAIL"],
        "client_id": st.secrets["GOOGLE_SHEETS_CLIENT_ID"],
        "token_uri": "https://oauth2.googleapis.com/token",
    }


def _get_worksheet():
    """获取（或初始化）Google Sheet 的第一个 worksheet。"""
    global _WORKSHEET  # noqa: PLW0603

    if not _ANALYTICS_LIB_OK:
        return None

    if _WORKSHEET is not None:
        return _WORKSHEET

    sheet_id = st.secrets.get("GOOGLE_SHEET_ID", None)
    if not sheet_id:
        # 没配 sheet id，就直接跳过埋点，不打扰用户
        return None

    info = _load_service_account_info()
    if info is None:
        # 没有可用的 service account 配置
        return None

    try:
        creds = service_account.Credentials.from_service_account_info(
            info,
            scopes=["https://www.googleapis.com/auth/spreadsheets"],
        )
        client = gspread.authorize(creds)
        sh = client.open_by_key(sheet_id)
        ws = sh.sheet1

        # 如果是空表，写入表头
        if not ws.get_all_values():
            ws.append_row(
                ["timestamp", "event", "session_id", "detail"],
                value_input_option="RAW",
            )

        _WORKSHEET = ws
        return ws
    except Exception as e:  # noqa: BLE001
        st.warning(f"⚠️ Analytics 初始化失败：{e}", icon="⚠️")
        return None


def log_event(
    event: str,
    session_state: Optional[Dict[str, Any]] = None,
    detail: str = "",
) -> None:
    """
    记录一个事件。

    参数：
    - event: 事件名，例如 "page_view" / "generate_success" / "feedback"
    - session_state: 可以传 st.session_state 或你自己的 dict，用来提取 sid（会话 id）
    - detail: 额外信息（例如错误信息、反馈内容等）
    """
    ws = _get_worksheet()
    if ws is None:
        return

    sid = ""
    if session_state is not None:
        # 兼容 dict 和 st.session_state 两种写法
        try:
            sid = session_state.get("sid", "")
        except Exception:  # noqa: BLE001
            sid = getattr(session_state, "sid", "")

    row = [
        datetime.datetime.utcnow().isoformat(),
        event,
        sid,
        detail,
    ]

    try:
        ws.append_row(row, value_input_option="RAW")
    except Exception:
        # 不让埋点影响主流程，静默失败
        pass