# analytics.py
from __future__ import annotations

import json
import os
import uuid
from datetime import datetime
from typing import Any, Dict, Optional

import streamlit as st

# 可选依赖：Google Sheet
try:
    import gspread
    from google.oauth2.service_account import Credentials

    HAS_GSHEET = True
except Exception:
    HAS_GSHEET = False

# 可选依赖：Slack
try:
    import requests  # 确保 requirements.txt 里有 requests
except Exception:  # 理论上不会，但防御一下
    requests = None


# =============== 基础工具 ===============

def _get_session_id() -> str:
    """在当前 session_state 中分配一个匿名访客 ID。"""
    if "sid" not in st.session_state:
        st.session_state["sid"] = str(uuid.uuid4())
    return st.session_state["sid"]


def _utc_iso() -> str:
    return datetime.utcnow().isoformat()


# =============== Google Sheet 相关 ===============

def _get_gsheet_worksheet(sheet_name: str = "events"):
    """
    返回指定名称的 Worksheet，没有就创建。
    如果环境变量或依赖不完整，返回 None。
    """
    if not HAS_GSHEET:
        return None

    raw_json = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON")
    sheet_id = os.getenv("GOOGLE_SHEET_ID")

    if not raw_json or not sheet_id:
        return None

    try:
        info = json.loads(raw_json)
        scopes = ["https://www.googleapis.com/auth/spreadsheets"]
        creds = Credentials.from_service_account_info(info, scopes=scopes)
        client = gspread.authorize(creds)
        sh = client.open_by_key(sheet_id)
        try:
            ws = sh.worksheet(sheet_name)
        except gspread.WorksheetNotFound:
            ws = sh.add_worksheet(title=sheet_name, rows=1000, cols=20)
        return ws
    except Exception:
        # 所有错误都静默，避免影响主业务
        return None


def _append_row(sheet_name: str, row: list[Any]) -> None:
    """往指定 sheet 追加一行，失败时静默。"""
    try:
        ws = _get_gsheet_worksheet(sheet_name)
        if ws is None:
            return
        ws.append_row(row, value_input_option="RAW")
    except Exception:
        # 不让任何异常冒出去
        return


# =============== Slack 通知 ===============

def send_slack_notification(text: str) -> None:
    """
    发送一条 Slack 通知。
    如果没有配置 SLACK_WEBHOOK_URL 或 requests 不可用，则静默。
    """
    webhook = os.getenv("SLACK_WEBHOOK_URL")
    if not webhook or requests is None:
        return

    try:
        requests.post(webhook, json={"text": text}, timeout=5)
    except Exception:
        return


# =============== 埋点与反馈接口（供 app.py 调用） ===============

def log_event(event_type: str, meta: Optional[Dict[str, Any]] = None) -> None:
    """
    普通事件埋点：页面浏览、生成成功、生成失败等。
    会尝试写入 Google Sheet 的 `events` 工作表。
    """
    try:
        sid = _get_session_id()
        now = _utc_iso()
        meta = meta or {}

        # 本地留一份（调试时方便查看）
        events = st.session_state.get("_events", [])
        events.append(
            {
                "sid": sid,
                "ts": now,
                "type": event_type,
                "meta": meta,
            }
        )
        st.session_state["_events"] = events

        # 写入 Google Sheet
        _append_row(
            "events",
            [
                now,
                sid,
                event_type,
                json.dumps(meta, ensure_ascii=False),
            ],
        )
    except Exception:
        # 保底，防止任何异常影响主流程
        return


def log_feedback(
    feedback_text: str,
    contact: str | None = None,
    meta: Optional[Dict[str, Any]] = None,
) -> None:
    """
    用户主动提交的产品反馈。
    - feedback_text：反馈内容（必填）
    - contact：邮箱/微信/小红书 ID（选填）
    """
    if not feedback_text.strip():
        return

    try:
        sid = _get_session_id()
        now = _utc_iso()
        meta = meta or {}

        _append_row(
            "feedback",
            [
                now,
                sid,
                feedback_text,
                contact or "",
                json.dumps(meta, ensure_ascii=False),
            ],
        )

        # 可选：来一条 Slack 提醒你有人留言了
        send_slack_notification(
            f"📝 新用户反馈：\n"
            f"- SID: {sid}\n"
            f"- Contact: {contact or 'N/A'}\n"
            f"- 内容: {feedback_text[:500]}"
        )
    except Exception:
        return


def log_error(
    location: str,
    error: Exception,
    meta: Optional[Dict[str, Any]] = None,
) -> None:
    """
    关键报错收集：在你自己的 try/except 里调用。
    - location：字符串，说明在哪个步骤出错（例如 'generate_cv'）
    - error：异常对象
    """
    try:
        sid = _get_session_id()
        now = _utc_iso()
        meta = meta or {}

        # 写入 Google Sheet
        _append_row(
            "errors",
            [
                now,
                sid,
                location,
                repr(error),
                json.dumps(meta, ensure_ascii=False),
            ],
        )

        # 发 Slack 报警
        send_slack_notification(
            f"⚠️ 产品报错（{location}）\n"
            f"- SID: {sid}\n"
            f"- 时间: {now}\n"
            f"- 错误: {repr(error)[:800]}"
        )
    except Exception:
        return