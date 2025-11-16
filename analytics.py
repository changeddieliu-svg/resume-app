import json
import datetime
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import streamlit as st


# ============================================================
# Helper — load Google credentials from Streamlit secrets
# ============================================================
def _get_google_client():
    try:
        service_json_str = st.secrets["GOOGLE_SERVICE_ACCOUNT_JSON"]
        service_json = json.loads(service_json_str)

        sheet_id = st.secrets["GOOGLE_SHEET_ID"]

        scope = [
            "https://spreadsheets.google.com/feeds",
            "https://www.googleapis.com/auth/drive"
        ]

        credentials = ServiceAccountCredentials.from_json_keyfile_dict(service_json, scope)
        client = gspread.authorize(credentials)
        sheet = client.open_by_key(sheet_id)

        return sheet

    except Exception as e:
        print("⚠ Failed to init Google Sheets:", e)
        return None


# ============================================================
# Write event to a sheet tab safely
# ============================================================
def _safe_append(sheet_name, row_values):
    try:
        sheet = _get_google_client()
        if sheet is None:
            print("⚠ No Google sheet client (skip write)")
            return

        try:
            worksheet = sheet.worksheet(sheet_name)
        except:
            # If worksheet does not exist, create it
            worksheet = sheet.add_worksheet(title=sheet_name, rows=5000, cols=20)

        worksheet.append_row(row_values)
    except Exception as e:
        print("⚠ Failed writing to Google Sheet:", e)


# ============================================================
# Public API for app
# ============================================================

def log_page_view():
    """Log when a user loads the main UI."""
    timestamp = datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")

    user = "anonymous"
    try:
        if hasattr(st, "experimental_user"):
            user_info = st.experimental_user
            if user_info and user_info.get("email"):
                user = user_info["email"]
    except:
        pass

    _safe_append("page_views", [timestamp, user])


def log_generate_event():
    """Log when a user clicks the 'Generate' button."""
    timestamp = datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")

    user = "anonymous"
    try:
        if hasattr(st, "experimental_user"):
            user_info = st.experimental_user
            if user_info and user_info.get("email"):
                user = user_info["email"]
    except:
        pass

    _safe_append("generation_events", [timestamp, user])


def log_feedback(text):
    """Log user feedback"""
    if not text:
        return

    timestamp = datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")

    user = "anonymous"
    try:
        if hasattr(st, "experimental_user"):
            user_info = st.experimental_user
            if user_info and user_info.get("email"):
                user = user_info["email"]
    except:
        pass

    _safe_append("user_feedback", [timestamp, user, text])


def log_error(error_text):
    """Log app errors so you can debug real user issues."""
    if not error_text:
        return

    timestamp = datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")

    _safe_append("errors", [timestamp, error_text])