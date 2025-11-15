# analytics.py
# 简单 Google Sheet + Slack 埋点，带安全的 session_id 处理

import os
import json
from datetime import datetime
from uuid import uuid4

import streamlit as st

# ============== Session 处理（修复 KeyError 的关键）==============

def get_session_id() -> str:
    """
    确保每个浏览器会话有一个稳定的 session_id。
    如果 session_state 里还没有，就自动生成一个。
    """
    if "sid" not in st.session_state:
        st.session_state["sid"] = str(uuid4())
    return st.session_state["sid"]


# ============== Google Sheet 相关（可选，不配置也能跑）==============

def _get_gsheet_client():
    """
    使用 Streamlit secrets 里的服务账号 JSON 创建 gspread client。
    如果没配置，就返回 None，只在日志里提示，不中断应用。
    """
    try:
        import gspread  # 只有真的要用的时候才 import
    except ImportError:
        # requirements.txt 里没装 gspread 的情况下，直接跳过
        st.warning("gspread 未安装，暂不记录分析数据。")
        return None

    service_account_info = st.secrets.get("GOOGLE_SERVICE_ACCOUNT_JSON")
    if not service_account_info:
        # 你还没在 secrets 里配置这一项
        return None

    if isinstance(service_account_info, str):
        # 有些人会把 JSON 直接作为字符串存到 secrets
        service_account_info = json.loads(service_account_info)

    return gspread.service_account_from_dict(service_account_info)


def _get_worksheet():
    """
    获取 Google Sheet 的第一个 worksheet。
    如果没配置 Sheet ID，返回 None。
    """
    sheet_id = st.secrets.get("ANALYTICS_SHEET_ID")
    if not sheet_id:
        return None

    client = _get_gsheet_client()
    if client is None:
        return None

    try:
        sh = client.open_by_key(sheet_id)
        ws = sh.sheet1
        return ws
    except Exception as e:
        # 不影响主流程，只是提示
        st.toast(f"⚠️ 分析数据暂时无法写入：{e}", icon="⚠️")
        return None


# ============== Slack 通知（可选）==============

def send_slack_notification(text: str):
    """
    如果在 secrets 里配置了 SLACK_WEBHOOK_URL，就发一条消息到 Slack。
    不配置就静默跳过。
    """
    webhook_url = st.secrets.get("SLACK_WEBHOOK_URL")
    if not webhook_url:
        return

    try:
        import requests
        requests.post(webhook_url, json={"text": text}, timeout=5)
    except Exception:
        # 不要因为 Slack 挂了拖垮主应用
        pass


# ============== 对外主接口：记录事件 ==============

def log_event(event_type: str, meta: dict | None = None):
    """
    记录一次埋点事件：
    - event_type: 例如 "page_view", "generate_clicked", "api_fallback"
    - meta: 任意附加信息（字典），会以 JSON 存到表里
    """
    # 1. 确保有 session_id —— 这是这次修复的关键
    sid = get_session_id()

    # 2. 准备行数据
    ts = datetime.utcnow().isoformat()
    meta_json = json.dumps(meta or {}, ensure_ascii=False)

    # 可以顺便记录一下 user agent（但在 Streamlit Cloud 上往往拿不到太多）
    user_agent = st.session_state.get("_user_agent", "")

    row = [ts, sid, event_type, user_agent, meta_json]

    # 3. 写入 Google Sheet（如果有配置）
    ws = _get_worksheet()
    if ws is not None:
        try:
            ws.append_row(row, value_input_option="RAW")
        except Exception as e:
            # 只在你自己用的时候提示一下，不要影响用户体验
            if st.session_state.get("_dev_mode"):
                st.warning(f"写入分析数据失败：{e}")

    # 4. 特定事件触发 Slack 通知（例如 API 降级等）
    if event_type == "api_fallback":
        send_slack_notification(f"⚠️ OpenAI API 降级为 Demo：{meta_json}")