# -*- coding: utf-8 -*-
# app.py — AI Resume Optimizer (Streamlit + Google Sheets analytics + Slack alerts)
# Requires: streamlit, pdfplumber, python-docx, gspread, oauth2client, requests

import io, os, re, time
from datetime import datetime
from typing import Optional

import streamlit as st
import pdfplumber
from docx import Document

# --- Analytics & Alerts (from analytics.py) ---
from analytics import (
    log_event,
    log_feedback,
    call_model_with_fallback,
    notify_admin,
)

# ========== Page config ==========
st.set_page_config(
    page_title="AI 智能简历优化",
    page_icon="🧠",
    layout="wide",
    initial_sidebar_state="expanded",
    menu_items={ "Get Help": None, "Report a bug": None, "About": None },
)

# ========== CSS: remove top whitespace, align sidebar '设置' with main title, hide menu ==========
st.markdown("""
<style>
header [data-testid="stToolbar"],
header [data-testid="stActionButtonIcon"],
header [data-testid="stDeployButton"],
header [data-testid="baseButton-headerNoPadding"],
header .stAppHeaderRight { display: none !important; }

[data-testid="stHeader"] {
  visibility: hidden !important; height: 0 !important; min-height: 0 !important;
  padding: 0 !important; margin: 0 !important; background: transparent !important;
}

.appview-container .main .block-container {
  padding-top: 0.4rem !important;   /* right column title vertical position */
  padding-bottom: 0.8rem !important;
  max-width: 1200px !important; margin: 0 auto !important;
}

[data-testid="stSidebar"] .block-container {
  padding-top: 0.35rem !important;  /* left '设置' vertical position */
  padding-bottom: 0.6rem !important;
}

h1, h2, h3 { margin-top: 0.1rem !important; margin-bottom: 0.4rem !important; }
[data-testid="stFileUploader"] small { display: none !important; }
[data-testid="stFileUploader"] { margin-bottom: 0.4rem !important; }

button[kind="primary"] {
  font-weight: 600 !important; border-radius: 6px !important;
  padding: 0.55rem 0 !important; font-size: 1rem !important;
}

.tip-box {
  background: rgba(130,130,130,0.08);
  border: 1px dashed rgba(130,130,130,0.35);
  padding: 0.65rem 0.9rem; border-radius: 8px;
  font-size: 0.92rem; line-height: 1.5;
}

[data-testid="stDecoration"] { display:none !important; }  /* optional footer stripe */
</style>
""", unsafe_allow_html=True)

# ========== Constants ==========
ALLOWED_EXTS = {"pdf", "docx"}
MAX_FILE_MB = 50
MAX_FILE_BYTES = MAX_FILE_MB * 1024 * 1024

# ========== Utilities ==========
def file_too_large(file) -> bool:
    try:
        size = getattr(file, "size", None)
        if size is None:
            pos = file.tell()
            data = file.read()
            file.seek(pos)
            size = len(data or b"")
        return size > MAX_FILE_BYTES
    except Exception:
        return False

def read_docx(file) -> str:
    doc = Document(file)
    parts = [p.text.strip() for p in doc.paragraphs if p.text.strip()]
    return "\n".join(parts)

def read_pdf(file, use_ocr=False) -> str:
    text = []
    try:
        with pdfplumber.open(file) as pdf:
            for page in pdf.pages:
                t = page.extract_text() or ""
                if t.strip():
                    text.append(t)
    except Exception:
        pass
    full = "\n".join(text).strip()
    if not full and use_ocr:
        full = "[OCR占位] 当前为扫描件简历，占位示例文本。"
    return full

def detect_language(text: str) -> str:
    cjk = re.findall(r'[\u4e00-\u9fa5]', text)
    letters = re.findall(r'[A-Za-z]', text)
    return "zh" if len(cjk) >= len(letters) else "en"

def make_docx_bytes(content: str, title="resume") -> bytes:
    doc = Document()
    for line in content.splitlines():
        doc.add_paragraph(line)
    bio = io.BytesIO()
    doc.save(bio)
    return bio.getvalue()

def build_demo_optimized(resume_text, jd_text, highlights, extra, lang):
    bullet = "•" if lang == "en" else "·"
    title = "Optimized Resume (Demo)" if lang == "en" else "优化简历（演示版）"
    sug = "Highlights" if lang == "en" else "亮点聚焦"
    req = "JD / Instruction" if lang == "en" else "目标JD / 指令"
    out = [f"{title}", "", f"{sug}:"]
    for h in (highlights or []):
        out.append(f"{bullet} {h}")
    if extra.strip():
        out.append(f"{bullet} {extra.strip()}")
    out += ["", f"{req}:", (jd_text or "").strip() or "(无)"]
    out += ["", "—— 以下为原始内容提取 ——", resume_text[:2500]]
    return "\n".join(out).strip()

def build_demo_cover_letter(resume_text, jd_text, lang):
    if lang == "en":
        return (
            "Cover Letter (Demo)\n\n"
            "Dear Hiring Manager,\n\n"
            "I am writing to express my strong interest in this role. "
            "With hands-on experience in data analysis and problem-solving, "
            "I believe my background aligns well with the JD.\n\n"
            "Sincerely,\nYour Name"
        )
    else:
        return (
            "【求职信（演示版）】\n\n"
            "尊敬的招聘负责人：\n\n"
            "您好！我对该岗位非常感兴趣。基于我在数据分析、问题解决等方面的经历，"
            "我与岗位职责高度匹配。感谢您的时间与考虑！\n\n"
            "此致\n敬礼\n候选人"
        )

# Optional real OpenAI call (will be wrapped by call_model_with_fallback)
def generate_with_openai(prompt: str) -> str:
    """
    If OPENAI_API_KEY is set in Streamlit Secrets, this will try to call OpenAI.
    Otherwise it raises to trigger the demo fallback.
    """
    api_key = st.secrets.get("OPENAI_API_KEY", "")
    if not api_key:
        raise RuntimeError("no_openai_key")
    try:
        # openai sdk v1
        from openai import OpenAI
        client = OpenAI(api_key=api_key)
        r = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "You are a concise, high-quality resume optimizer."},
                {"role": "user", "content": prompt},
            ],
            temperature=0.35,
        )
        return r.choices[0].message.content.strip()
    except Exception as e:
        # propagate to fallback wrapper
        raise

def build_prompt(resume_text, jd_text, lang, focus_tags, notes, need_cover):
    zh = (lang == "zh")
    lines = []
    if zh:
        lines.append("你是一名资深简历顾问，请优化下列简历，使其更符合目标职位。输出中文优化简历。")
    else:
        lines.append("You are a professional resume consultant. Improve the resume to match the JD and output in English.")
    if focus_tags:
        lines.append(("精修侧重：" if zh else "Focus: ") + ", ".join(focus_tags))
    if notes.strip():
        lines.append(("增强点：" if zh else "Additional notes: ") + notes.strip())
    if need_cover:
        lines.append("并在最后生成一份求职信。")
    lines.append(("\n【原始简历】\n" if zh else "\n[Resume]\n") + resume_text.strip())
    if jd_text.strip():
        lines.append(("\n【目标职位】\n" if zh else "\n[Target JD]\n") + jd_text.strip())
    return "\n".join(lines)

# ========== Track page view ==========
log_event("page_view")

# ========== Sidebar ==========
with st.sidebar:
    st.subheader("设置")
    st.caption("（左侧选项仅影响生成时的强调方向）")

    tags = ["业务影响", "量化指标", "数据驱动", "模型能力", "沟通协作", "项目管理", "客户导向", "领导力", "编程能力", "研究分析"]
    selected_tags = st.multiselect("精修侧重（可多选）", tags, default=["业务影响"])
    extra_points = st.text_area("增强点（可自定义）", placeholder="如：强调量化成果/沟通影响；写作更正式/口语化；偏数据岗/产品岗等…", height=110)
    want_cl = st.checkbox("生成求职信（Cover Letter）", value=True)
    use_ocr = st.checkbox("启用 OCR（扫描PDF）", value=False)
    st.markdown("---")
    st.caption("仅供个人求职使用，禁止商用与爬取。")

# ========== Main ==========
st.markdown("## 🧠 AI 智能简历优化")

col_left, col_right = st.columns([1, 1], gap="small")

with col_left:
    st.markdown("### 上传简历（PDF 或 DOCX）")
    resume_file = st.file_uploader("上传文件", type=list(ALLOWED_EXTS), label_visibility="collapsed")
    st.caption(f"支持 PDF / DOCX · 单文件 ≤ {MAX_FILE_MB}MB · 扫描件可启用 OCR")

with col_right:
    st.markdown("### 粘贴目标职位 JD 或优化指令（可批量、空行分隔）")
    jd_text = st.text_area(
        "示例：Actuarial graduate role at Deloitte. 请突出数据分析与建模能力；Cover Letter 要更正式。",
        placeholder="可以粘贴 JD，也可以直接写优化指令（如强调哪些技能、写作风格、偏行业等）",
        height=160,
        label_visibility="collapsed"
    )

st.markdown('<div class="tip-box">💡 提示：可在左侧设置“精修侧重/增强点”；若PDF为扫描件，可开启OCR。</div>', unsafe_allow_html=True)
st.write("")
generate_btn = st.button("🚀 一键生成", type="primary", use_container_width=True)

out_box = st.container()

# ========== Generate ==========
if generate_btn:
    if not resume_file:
        st.error("请先上传简历文件（仅支持 PDF/DOCX，≤ 50MB）。")
        st.stop()

    ext = (resume_file.name.split(".")[-1] or "").lower()
    if ext not in ALLOWED_EXTS:
        st.error("仅支持 PDF / DOCX 文件。")
        st.stop()

    if file_too_large(resume_file):
        st.error(f"文件过大：当前文件超过 {MAX_FILE_MB}MB 限制。")
        st.stop()

    with st.spinner("正在解析简历…"):
        resume_text = read_docx(resume_file) if ext == "docx" else read_pdf(resume_file, use_ocr)

    if not resume_text.strip():
        st.error("未能读取到简历内容。如为扫描件，请尝试启用 OCR。")
        st.stop()

    lang = detect_language(resume_text)

    # event: user clicked generate
    log_event("generate_click",
              file_size=getattr(resume_file, "size", None),
              ocr=use_ocr,
              jd_len=len(jd_text or ""),
              lang=lang)

    # Build prompt
    prompt = build_prompt(
        resume_text=resume_text,
        jd_text=jd_text or "",
        lang=lang,
        focus_tags=selected_tags,
        notes=extra_points or "",
        need_cover=want_cl
    )

    # Call model with quota-aware fallback
    def _real_call():
        return generate_with_openai(prompt)

    with st.spinner("正在生成优化简历…"):
        result_text, used_demo = call_model_with_fallback(
            _real_call,
            context={"lang": lang, "jd_len": len(jd_text or ""), "file_size": getattr(resume_file, "size", None), "ocr": use_ocr}
        )

    if used_demo or not result_text:
        result_text = build_demo_optimized(resume_text, jd_text, selected_tags, extra_points, lang)
        st.info("⚠️ 当前使用演示输出（API 配额或速率限制）。管理员已收到通知。")

    # Show + downloads
    with out_box:
        st.subheader("✅ 优化简历预览")
        st.text_area("", result_text, height=300, label_visibility="collapsed")

        docx_bytes = make_docx_bytes(result_text, "optimized_resume")
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        if st.download_button("⬇️ 下载 DOCX",
                              data=docx_bytes,
                              file_name=f"Optimized_Resume_{ts}.docx",
                              mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                              use_container_width=True):
            log_event("download_docx")

    # Optional cover letter
    if want_cl:
        with st.spinner("正在生成求职信…"):
            if used_demo:
                cover_letter = build_demo_cover_letter(resume_text, jd_text, lang)
            else:
                # Try LLM again; if fails, fallback demo
                def _cl_call():
                    # Small prompt for cover letter based on same input
                    p = (f"Write a concise cover letter in {'Chinese' if lang=='zh' else 'English'} "
                         f"based on this resume and JD. Tone: professional, specific, one page.\n\n"
                         f"[Resume]\n{resume_text}\n\n[JD]\n{jd_text}")
                    return generate_with_openai(p)
                try:
                    cover_letter, used_demo2 = call_model_with_fallback(
                        _cl_call,
                        context={"lang": lang, "type": "cover_letter"}
                    )
                    if used_demo2 or not cover_letter:
                        cover_letter = build_demo_cover_letter(resume_text, jd_text, lang)
                except Exception:
                    cover_letter = build_demo_cover_letter(resume_text, jd_text, lang)

        st.subheader("📄 求职信（可选）")
        st.text_area("", cover_letter, height=240, label_visibility="collapsed")
        cl_docx = make_docx_bytes(cover_letter, "cover_letter")
        if st.download_button("⬇️ 下载求职信 DOCX",
                              data=cl_docx,
                              file_name=f"Cover_Letter_{ts}.docx",
                              mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                              use_container_width=True):
            log_event("download_cover_docx")

# ========== Feedback ==========
st.markdown("### 这次结果有帮助吗？")
c1, c2 = st.columns(2)
with c1:
    if st.button("👍 有帮助"):
        log_feedback(rating="up"); log_event("feedback", note="up")
        st.success("感谢反馈！")
with c2:
    if st.button("👎 需要改进"):
        log_feedback(rating="down"); log_event("feedback", note="down")
        st.success("已记录～")

fb = st.text_area("写点建议给我（可选，100字内）", max_chars=300, height=90)
if st.button("提交建议"):
    txt = (fb or "").strip()
    if txt:
        log_feedback(comment=txt); log_event("feedback_text", note=f"{len(txt)} chars")
        notify_admin(f"💬 Feedback: {txt[:200]}")
        st.success("收到！非常感谢～")

# ========== (Optional) Admin panel ==========
with st.sidebar.expander("Admin login"):
    admin_try = st.text_input("Enter admin code", type="password")
    admin_mode = (admin_try and admin_try == st.secrets.get("ADMIN_CODE"))

if admin_mode:
    st.markdown("## 🔐 Admin")
    st.caption("Metrics powered by Google Sheets (events / feedback). Open the Sheet for full data & charts.")
    st.info("Tip: Use Google Sheets filters to compute DAU/WAU; this app logs all events to the 'events' tab.")