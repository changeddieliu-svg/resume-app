# analytics.py
# Google Sheets logging + Slack alerts + quota/rate-limit fallback
# Requirements (in requirements.txt):
#   gspread
#   oauth2client
#   requests

import json
import time
import datetime as dt
from uuid import uuid4
from typing import Any, Dict, Optional, Tuple

import streamlit as st

# Optional deps – only used for logging; app won't crash if auth fails
try:
    import gspread
    from oauth2client.service_account import ServiceAccountCredentials
except Exception:  # pragma: no cover
    gspread = None
    ServiceAccountCredentials = None

try:
    import requests
except Exception:  # pragma: no cover
    requests = None


# -------------------- Session identity --------------------
# Stable anonymous session id for per-user frequency/DAU
st.session_state.setdefault("sid", str(uuid4()))


# -------------------- Config via Streamlit Secrets --------------------
SHEET_ID: str = st.secrets.get("SHEET_ID", "")
GCP_SA_JSON: dict = st.secrets.get("GCP_SERVICE_ACCOUNT", {}) or {}
SLACK_WEBHOOK: str = st.secrets.get("SLACK_WEBHOOK", "")

# Tab names in your Google Sheet (must exist with headers)
TAB_EVENTS = "events"     # headers: timestamp, sid, event_type, lang, file_size, ocr, jd_len, latency_ms, note
TAB_FEEDBACK = "feedback" # headers: timestamp, sid, rating, comment
TAB_FLAGS = "flags"       # headers: timestamp, sid, key, value, note


# -------------------- Google Sheet helpers --------------------
@st.cache_resource(show_spinner=False)
def _sheet_client():
    """Authorize and open the Google Sheet. Cached for the process lifetime."""
    if not (gspread and ServiceAccountCredentials and SHEET_ID and GCP_SA_JSON):
        return None
    scope = ["https://www.googleapis.com/auth/spreadsheets"]
    creds = ServiceAccountCredentials.from_json_keyfile_dict(GCP_SA_JSON, scope)
    gc = gspread.authorize(creds)
    return gc.open_by_key(SHEET_ID)

def _append_row(tab: str, row: list) -> None:
    """Append a single row. Never crash the UI if logging fails."""
    try:
        sh = _sheet_client()
        if not sh:
            return
        ws = sh.worksheet(tab)
        ws.append_row(row, value_input_option="USER_ENTERED")
    except Exception as e:  # pragma: no cover
        # Silent fail – you can print if needed:
        # print("Sheet append failed:", e)
        pass


# -------------------- Public logging API --------------------
def log_event(event_type: str, **props: Any) -> None:
    """
    Log an application event to the 'events' tab.
    Common fields you may pass in props:
      - lang ("en"/"zh"), file_size (int), ocr (bool), jd_len (int), latency_ms (int), note (str)
    """
    row = [
        dt.datetime.utcnow().isoformat(timespec="seconds") + "Z",
        st.session_state["sid"],
        event_type,
        str(props.get("lang", "")),
        str(props.get("file_size", "")),
        str(props.get("ocr", "")),
        str(props.get("jd_len", "")),
        str(props.get("latency_ms", "")),
        json.dumps({
            k: v for k, v in props.items()
            if k not in {"lang", "file_size", "ocr", "jd_len", "latency_ms"}
        }, ensure_ascii=False) or "",
    ]
    _append_row(TAB_EVENTS, row)

def log_feedback(rating: Optional[str] = None, comment: str = "") -> None:
    """Log thumbs (rating='up'/'down') and/or free-text comment to 'feedback'."""
    row = [
        dt.datetime.utcnow().isoformat(timespec="seconds") + "Z",
        st.session_state["sid"],
        (rating or ""),
        (comment or "")[:500],
    ]
    _append_row(TAB_FEEDBACK, row)

def set_flag(key: str, value: Any, note: str = "") -> None:
    """Write a key/value flag to 'flags' (e.g., last_quota_fallback)."""
    row = [
        dt.datetime.utcnow().isoformat(timespec="seconds") + "Z",
        st.session_state["sid"],
        key,
        json.dumps(value, ensure_ascii=False),
        note,
    ]
    _append_row(TAB_FLAGS, row)


# -------------------- Admin notifications (Slack) --------------------
def notify_admin(text: str) -> None:
    """Send a Slack message if SLACK_WEBHOOK is configured."""
    if not (SLACK_WEBHOOK and requests):
        return
    try:
        requests.post(SLACK_WEBHOOK, data=json.dumps({"text": text}), timeout=5)
    except Exception:  # pragma: no cover
        pass


# -------------------- Quota-aware wrapper --------------------
def call_model_with_fallback(call_fn, *, context: Optional[Dict[str, Any]] = None) -> Tuple[Optional[str], bool]:
    """
    Execute `call_fn()` (your real OpenAI call). On quota/rate/billing errors:
      - log generated_demo
      - set a flag
      - notify admin (only once per session)
      - return (None, True) so caller can use demo output
    Returns: (text_or_none, used_demo: bool)
    """
    t0 = time.time()
    try:
        out = call_fn()
        ms = int((time.time() - t0) * 1000)
        log_event("generated_ok", latency_ms=ms, **(context or {}))
        return out, False
    except Exception as e:
        msg = str(e).lower()
        quota_keywords = ("quota", "rate limit", "insufficient_quota", "billing", "invalid api key", "overloaded")
        if any(k in msg for k in quota_keywords):
            if not st.session_state.get("notified_quota"):
                notify_admin(f"🚨 OpenAI quota/rate fallback. sid={st.session_state['sid']} err={msg[:200]}")
                st.session_state["notified_quota"] = True
            log_event("generated_demo", note="quota_or_rate", **(context or {}))
            set_flag("last_quota_fallback", {"err": msg[:200]})
            return None, True
        # Other errors – record and re-raise for UI to handle
        log_event("error", note=msg[:200], **(context or {}))
        raise