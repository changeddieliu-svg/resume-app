import json
from datetime import datetime
from typing import Any, Dict, Optional

import streamlit as st

# 依赖：gspread, google-auth
# 确保 requirements.txt 里包含：
# gspread
# google-auth

import gspread
from google.oauth2.service_account import Credentials

# 只需要访问当前这张表
SCOPES = ["https://www.googleapis.com/auth/spreadsheets.readonly",
          "https://www.googleapis.com/auth/spreadsheets"]


# ---------- 基础工具 ----------

@st.cache_resource(show_spinner=False)
def _get_gspread_client() -> gspread.Client:
    """
    用 service account JSON + scopes 创建 gspread client。
    JSON 来自 st.secrets["GOOGLE_SERVICE_ACCOUNT_JSON"]。
    """
    raw = st.secrets.get("GOOGLE_SERVICE_ACCOUNT_JSON")
    if raw is None:
        raise RuntimeError("Missing GOOGLE_SERVICE_ACCOUNT_JSON in st.secrets")

    if isinstance(raw, str):
        info = json.loads(raw)
    else:
        # 如果在本地开发时用 TOML 的 [[GOOGLE_SERVICE_ACCOUNT_JSON]] 结构，也兼容
        info = dict(raw)

    creds = Credentials.from_service_account_info(info, scopes=SCOPES)
    return gspread.authorize(creds)


@st.cache_resource(show_spinner=False)
def _get_spreadsheet() -> gspread.Spreadsheet:
    client = _get_gspread_client()
    sheet_id = st.secrets.get("GOOGLE_SHEET_ID")
    if not sheet_id:
        raise RuntimeError("Missing GOOGLE_SHEET_ID in st.secrets")
    return client.open_by_key(sheet_id)


def _ensure_worksheet(
    spreadsheet: gspread.Spreadsheet,
    title: str,
    header: list[str],
) -> gspread.Worksheet:
    """
    如果工作表不存在则创建，并在首行写入 header。
    已存在则直接返回。
    """
    try:
        ws = spreadsheet.worksheet(title)
    except gspread.WorksheetNotFound:
        # 创建新的 worksheet
        ws = spreadsheet.add_worksheet(title=title, rows="1000", cols=str(len(header) + 5))
        ws.append_row(header)
    else:
        # 如果第一行是空的，也补上表头（防止手滑清空）
        try:
            first_row = ws.row_values(1)
        except Exception:
            first_row = []
        if not any(first_row):
            ws.append_row(header)
    return ws


def _base_fields() -> list[Any]:
    """所有日志记录共用的一些字段。"""
    now = datetime.utcnow().isoformat(timespec="seconds") + "Z"

    sid = st.session_state.get("sid") or st.session_state.get("_session_id") or ""
    user = st.session_state.get("user_email") or st.session_state.get("username") or "anonymous"

    return [now, sid, user]


def _safe_json(data: Optional[Dict[str, Any]]) -> str:
    if not data:
        return ""
    try:
        return json.dumps(data, ensure_ascii=False)
    except Exception:
        return str(data)


# ---------- 对外暴露的记录函数 ----------

def log_usage(event: str, extra: Optional[Dict[str, Any]] = None) -> None:
    """
    记录正常使用行为：
    - 页面浏览
    - 点击一键生成
    - 导出文件 等等
    """
    try:
        ss = _get_spreadsheet()
        ws = _ensure_worksheet(
            ss,
            "Usage_Log",
            ["timestamp_utc", "session_id", "user", "event", "extra_json"],
        )
        row = _base_fields() + [event, _safe_json(extra)]
        ws.append_row(row)
    except Exception as e:
        # 不要因为埋点出错导致主功能崩溃
        print("[analytics] log_usage error:", e)


def log_error(location: str, error_message: str, extra: Optional[Dict[str, Any]] = None) -> None:
    """
    记录错误信息，方便你在 Sheet 里监控。
    location: 出错的大概位置，例如 "generate_documents" / "ocr_extract" 等
    """
    try:
        ss = _get_spreadsheet()
        ws = _ensure_worksheet(
            ss,
            "Error_Log",
            ["timestamp_utc", "session_id", "user", "location", "error_message", "extra_json"],
        )
        row = _base_fields() + [location, error_message, _safe_json(extra)]
        ws.append_row(row)
    except Exception as e:
        print("[analytics] log_error error:", e)


def log_feedback(
    feedback_text: str,
    rating: Optional[int] = None,
    contact: Optional[str] = None,
    extra: Optional[Dict[str, Any]] = None,
) -> None:
    """
    记录用户反馈：
    - feedback_text: 用户输入的评价/建议
    - rating: 可选评分（1-5）
    - contact: 可选联系方式（微信/邮箱等）
    """
    try:
        ss = _get_spreadsheet()
        ws = _ensure_worksheet(
            ss,
            "User_Feedback",
            [
                "timestamp_utc",
                "session_id",
                "user",
                "rating",
                "feedback_text",
                "contact",
                "extra_json",
            ],
        )
        row = _base_fields() + [rating if rating is not None else "", feedback_text, contact or "", _safe_json(extra)]
        ws.append_row(row)
    except Exception as e:
        print("[analytics] log_feedback error:", e)


def log_system_event(event: str, extra: Optional[Dict[str, Any]] = None) -> None:
    """
    记录系统级事件，例如：
    - "app_started"
    - "api_key_missing_fallback_demo"
    等。
    """
    try:
        ss = _get_spreadsheet()
        ws = _ensure_worksheet(
            ss,
            "System_Events",
            ["timestamp_utc", "session_id", "user", "event", "extra_json"],
        )
        row = _base_fields() + [event, _safe_json(extra)]
        ws.append_row(row)
    except Exception as e:
        print("[analytics] log_system_event error:", e)