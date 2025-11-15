"""
analytics.py
记录用户行为 & 反馈到 Google Sheet

依赖：
- streamlit
- gspread
- google-auth

需要在 Streamlit Secrets 中配置两部分：
[gcp_service_account]  # Google Service Account JSON
[analytics]
sheet_id = "你的 Google Sheet ID"
"""

from datetime import datetime
from typing import Optional

import streamlit as st

# 这两个库来自 google-auth 和 gspread
try:
    from google.oauth2.service_account import Credentials
    import gspread
    _HAS_GSHEETS = True
except ImportError:
    _HAS_GSHEETS = False
    print("⚠ Google Sheets 依赖未安装：请确认 requirements.txt 中包含 gspread 和 google-auth")


# ---------- 内部工具函数 ----------

def _get_sheet() -> Optional["gspread.Worksheet"]:
    """
    尝试连接到 Google Sheet
    如果配置不完整 / 依赖缺失，就返回 None（不让主程序报错）。
    """
    if not _HAS_GSHEETS:
        return None

    # 必须在 Secrets 中配置 gcp_service_account 和 analytics.sheet_id
    if "gcp_service_account" not in st.secrets or "analytics" not in st.secrets:
        return None

    try:
        creds_info = st.secrets["gcp_service_account"]
        sheet_id = st.secrets["analytics"]["sheet_id"]

        creds = Credentials.from_service_account_info(
            creds_info,
            scopes=["https://www.googleapis.com/auth/spreadsheets"],
        )
        client = gspread.authorize(creds)
        sheet = client.open_by_key(sheet_id).sheet1  # 默认使用第一个工作表
        return sheet
    except Exception as e:
        # 不让主应用崩掉，只是在日志里提示
        print("⚠ Analytics: 初始化 Google Sheet 失败：", e)
        return None


def _get_user_identifier() -> str:
    """
    尝试拿一个可以区分用户的标识。
    如果 app.py 里没设置 user_email，就退回到 'anonymous'。
    """
    # 如果你以后在 app.py 里设置：st.session_state["user_email"] = xxx，这里会自动用上
    user_email = st.session_state.get("user_email")
    if user_email:
        return str(user_email)

    # Streamlit Cloud 登录用户（如果有）
    user = st.runtime.scriptrunner.get_script_run_ctx()
    if user and getattr(user, "user", None):
        return str(user.user)

    return "anonymous"


def _append_row(event_type: str, details: str = "") -> None:
    """
    往 Google Sheet 追加一行记录。
    格式：时间戳 | 用户 | 事件类型 | 详情
    """
    sheet = _get_sheet()
    if sheet is None:
        # 没配置好就静默跳过，不影响主流程
        return

    try:
        timestamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
        user_id = _get_user_identifier()
        sheet.append_row([timestamp, user_id, event_type, details])
    except Exception as e:
        print("⚠ Analytics: 写入 Google Sheet 失败：", e)


# ---------- 对外暴露的函数：在 app.py 中调用 ----------

def log_event(event_type: str, details: str = "") -> None:
    """
    通用事件记录函数，用于：
    - 页面访问：log_event("page_view")
    - 生成简历：log_event("generate_resume", "success")
    - 生成失败：log_event("generate_resume_error", str(e))
    """
    _append_row(event_type, details)


def log_feedback(text: str) -> None:
    """
    用户反馈记录函数：
    - 在 app.py 中配合一个文本输入框 + 按钮调用
      log_feedback(feedback_text)
    """
    _append_row("feedback", text)


def log_error(context: str, error_msg: str) -> None:
    """
    错误记录函数（可选）：
    - log_error("generate_resume", str(e))
    """
    _append_row(f"error::{context}", error_msg)