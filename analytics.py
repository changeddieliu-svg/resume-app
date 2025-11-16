# analytics.py
import json
from datetime import datetime
from typing import Any, Dict, Tuple, Optional

import gspread
from google.oauth2.service_account import Credentials

# 下面这几个全局变量用来缓存 Google Sheet 连接
_gs_client: Optional[gspread.Client] = None
_usage_ws: Optional[gspread.Worksheet] = None
_feedback_ws: Optional[gspread.Worksheet] = None
_error_ws: Optional[gspread.Worksheet] = None


def _now_str() -> str:
    """统一的时间格式（UTC+0），方便在表里看。"""
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")


def init_analytics(secrets) -> Tuple[bool, str]:
    """
    初始化 Google Sheet 分析写入。
    - secrets: 一般传入的是 st.secrets
    - 返回 (ok, message)
        ok = True  表示初始化成功
        ok = False 表示失败，message 里带原因（给 UI 用）
    """
    global _gs_client, _usage_ws, _feedback_ws, _error_ws

    # 1) 取 JSON 配置
    try:
        json_str = secrets.get("GOOGLE_SERVICE_ACCOUNT_JSON", "")
    except Exception:
        return False, "未在 secrets 中找到 GOOGLE_SERVICE_ACCOUNT_JSON"

    if not json_str:
        return False, "GOOGLE_SERVICE_ACCOUNT_JSON 为空"

    # 2) 解析 JSON
    try:
        info = json.loads(json_str)
    except Exception as e:
        return False, f"GOOGLE_SERVICE_ACCOUNT_JSON 解析失败: {e}"

    # 3) 取 Sheet ID
    try:
        sheet_id = secrets.get("GOOGLE_SHEET_ID", "")
    except Exception:
        return False, "未在 secrets 中找到 GOOGLE_SHEET_ID"

    if not sheet_id:
        return False, "GOOGLE_SHEET_ID 为空"

    # 4) 构造凭证 & 客户端
    try:
        scopes = [
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/drive",
        ]
        creds = Credentials.from_service_account_info(info, scopes=scopes)
        client = gspread.authorize(creds)
        sh = client.open_by_key(sheet_id)
    except Exception as e:
        return False, f"连接 Google Sheet 失败: {e}"

    # 5) 获取 / 创建 worksheet
    def _get_or_create_ws(title: str, headers) -> gspread.Worksheet:
        try:
            ws = sh.worksheet(title)
        except gspread.WorksheetNotFound:
            ws = sh.add_worksheet(title=title, rows=1000, cols=len(headers))
            ws.append_row(headers)
        return ws

    try:
        _gs_client = client
        _usage_ws = _get_or_create_ws(
            "usage",
            ["timestamp", "session_id", "event", "detail_json"],
        )
        _feedback_ws = _get_or_create_ws(
            "feedback",
            ["timestamp", "session_id", "contact", "type", "content_json"],
        )
        _error_ws = _get_or_create_ws(
            "errors",
            ["timestamp", "session_id", "where", "error_msg"],
        )
    except Exception as e:
        return False, f"初始化 worksheet 失败: {e}"

    return True, "Analytics 已启用"


def log_event(event: str, session_id: str, detail: Dict[str, Any]):
    """记录一次使用事件到 usage 表。"""
    if _usage_ws is None:
        return  # Analytics 未启用就直接返回，不打断主流程
    try:
        _usage_ws.append_row(
            [
                _now_str(),
                session_id,
                event,
                json.dumps(detail, ensure_ascii=False),
            ]
        )
    except Exception:
        # 不要让任何异常影响主流程
        pass


def log_feedback(
    session_id: str,
    contact: str,
    fb_type: str,
    content: Dict[str, Any],
):
    """记录用户反馈到 feedback 表。"""
    if _feedback_ws is None:
        return
    try:
        _feedback_ws.append_row(
            [
                _now_str(),
                session_id,
                contact,
                fb_type,
                json.dumps(content, ensure_ascii=False),
            ]
        )
    except Exception:
        pass


def log_error(session_id: str, where: str, error_msg: str):
    """如果你愿意，也可以在主代码里捕获异常写到 errors 表。"""
    if _error_ws is None:
        return
    try:
        _error_ws.append_row(
            [
                _now_str(),
                session_id,
                where,
                error_msg,
            ]
        )
    except Exception:
        pass