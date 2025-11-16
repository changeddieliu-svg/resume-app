# analytics.py
import os
import json
from datetime import datetime

import gspread
from google.oauth2.service_account import Credentials

# ---------------------------------------------------------
# 读取 Streamlit Secrets 中的环境变量，拼成 service account
# ---------------------------------------------------------
def _build_service_account_info():
    project_id = os.getenv("GOOGLE_SHEETS_PROJECT_ID")
    private_key_id = os.getenv("GOOGLE_SHEETS_PRIVATE_KEY_ID")
    private_key = os.getenv("GOOGLE_SHEETS_PRIVATE_KEY")
    client_email = os.getenv("GOOGLE_SHEETS_CLIENT_EMAIL")
    client_id = os.getenv("GOOGLE_SHEETS_CLIENT_ID")

    # 任意一个缺失，就直接抛异常，后面会把 ANALYTICS_READY 设成 False
    if not all([project_id, private_key_id, private_key, client_email, client_id]):
        raise RuntimeError("Google Sheets 环境变量缺失，请检查 secrets 配置。")

    # 如果在 secrets 里是用 \n 存的，这里转回真正的换行
    private_key = private_key.replace("\\n", "\n")

    # 按 Google service account 标准结构拼 JSON
    info = {
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
    return info


def _get_sheet():
    """初始化 gspread client + 打开第一个工作表"""
    service_info = _build_service_account_info()

    creds = Credentials.from_service_account_info(
        service_info,
        scopes=["https://www.googleapis.com/auth/spreadsheets"],
    )

    gc = gspread.authorize(creds)

    sheet_id = os.getenv("GOOGLE_SHEETS_SHEET_ID")
    if not sheet_id:
        raise RuntimeError("GOOGLE_SHEETS_SHEET_ID 未配置。")

    sh = gc.open_by_key(sheet_id)
    worksheet = sh.sheet1  # 默认第一个 sheet，名称“工作表1”
    return worksheet


# 尝试初始化，全局复用一个 worksheet 对象
try:
    _sheet = _get_sheet()

    # 如果第一行是空的，就写上表头
    first_row = _sheet.row_values(1)
    if not first_row:
        _sheet.append_row(
            ["ts_utc", "event_type", "payload_json"],
            value_input_option="RAW",
        )

    ANALYTICS_READY = True
except Exception as e:
    # 这里不要抛出到页面，只是标记为不可用
    ANALYTICS_READY = False
    _sheet = None
    # 如需调试，可以暂时打印：
    # import traceback; traceback.print_exc()


def log_event(event_type: str, data: dict):
    """供 app.py 调用的统一埋点方法"""
    if not ANALYTICS_READY or _sheet is None:
        return

    try:
        ts = datetime.utcnow().isoformat()
        payload_str = json.dumps(data, ensure_ascii=False)
        _sheet.append_row(
            [ts, event_type, payload_str],
            value_input_option="RAW",
        )
    except Exception:
        # 为了不影响主流程，这里也静默失败，如需调试可以打印异常
        pass