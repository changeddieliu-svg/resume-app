# app.py
# AI 智能简历优化（自动识别语言：中文/英文 → 同语种输出；可生成 Cover Letter；DOCX 导出）
# 轻依赖：默认用 pdfplumber / python-docx；OCR 与 PDF 导出为可选能力（自动兜底，不安装也能跑）

import os
import io
import re
import json
import time
from typing import Optional, Tuple

import streamlit as st

# ---------- 可选：dotenv，优先用 Streamlit Secrets ----------
try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

# ---------- 文档解析 ----------
import pdfplumber
from docx import Document

# ---------- OCR（可选） ----------
_HAS_OCR = True
try:
    from pdf2image import convert_from_bytes
    import pytesseract
    from PIL import Image
except Exception:
    _HAS_OCR = False

# ---------- PDF 导出（可选） ----------
_HAS_PDF = True
try:
    from reportlab.pdfgen import canvas
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
except Exception:
    _HAS_PDF = False

# ---------- OpenAI SDK ----------
from openai import OpenAI

# =====================================================================
#                           配置 & 工具函数
# =====================================================================

def get_openai_client() -> OpenAI:
    """优先从 st.secrets 读取，其次读环境变量."""
    api_key = st.secrets.get("OPENAI_API_KEY", os.getenv("OPENAI_API_KEY", ""))
    if not api_key:
        st.error("未检测到 OPENAI_API_KEY，请在 Streamlit Secrets 或环境变量中配置。")
        st.stop()
    return OpenAI(api_key=api_key)

def get_model_name() -> str:
    return st.secrets.get("MODEL_NAME", os.getenv("MODEL_NAME", "gpt-4o-mini"))

# ----------------- 语言检测（EN/ZH） -----------------
try:
    from langdetect import detect as _langdetect
    _HAS_LANGDETECT = True
except Exception:
    _HAS_LANGDETECT = False

_ZH_HINTS = ["教育","项目","工作经历","个人信息","技能","职责","成就","成果","性别","出生","地址","电话","邮箱"]
_EN_HINTS = ["Education","Experience","Project","Work","Skills","Summary","Achievements","Responsibilities","Email","Phone","Address"]

def _ratio_non_ascii(text: str) -> float:
    if not text:
        return 0.0
    non_ascii = sum(1 for ch in text if ord(ch) > 127)
    return non_ascii / max(1, len(text))

def _contains_any(text: str, words) -> bool:
    t = text[:2000]
    return any(w in t for w in words)

def detect_lang_en_zh(text: str) -> str:
    """
    返回 'en' 或 'zh'。顺序：langdetect → 非ASCII比例 → 关键词启发 → 默认 'en'
    """
    t = (text or "").strip()

    if _HAS_LANGDETECT:
        try:
            code = _langdetect(t)
            if code.startswith("zh"): 
                return "zh"
            if code.startswith("en"):
                return "en"
        except Exception:
            pass

    if _ratio_non_ascii(t) > 0.25:
        return "zh"

    zh_hit = _contains_any(t, _ZH_HINTS)
    en_hit = _contains_any(t, _EN_HINTS)
    if zh_hit and not en_hit:
        return "zh"
    if en_hit and not zh_hit:
        return "en"
    return "en"

# ----------------- Prompt 模板 -----------------
EN_RESUME_PROMPT = """You are an expert resume editor. KEEP THE OUTPUT IN ENGLISH.
Rewrite the resume content to be concise, quantified and aligned to the target JD.
- Use strong action verbs and measurable outcomes
- Keep neutral tone for UK graduate/entry roles
- Do NOT invent experience
Return ONLY the optimized resume text.
"""

ZH_RESUME_PROMPT = """你是资深简历优化顾问。请全程使用【中文】输出，并保持专业、精炼、可量化、与目标JD高度匹配。
- 使用动词开头与量化结果
- 不新增或杜撰经历
- 不要输出解释或客套话
只返回【优化后的简历正文】。
"""

EN_CL_PROMPT = """Write a concise one-page UK-style cover letter in ENGLISH tailored to the target JD and the resume.
- Clear structure: opening, 2–3 achievements aligned to JD, closing
- Measurable results, no fluff, no repetition of resume
Return ONLY the letter text.
"""

ZH_CL_PROMPT = """请用【中文】撰写一页内的求职信，结合简历与目标JD：
- 结构清晰：开场、2–3条与JD高度匹配的量化成果、结尾
- 专业不堆词，不重复简历原句
只返回求职信正文。
"""

def get_prompts(lang: str):
    if lang == "zh":
        return ZH_RESUME_PROMPT, ZH_CL_PROMPT, "务必使用中文输出，且不要混用英文。"
    return EN_RESUME_PROMPT, EN_CL_PROMPT, "Always respond in English."

# =====================================================================
#                           解析简历
# =====================================================================

def read_docx(file_bytes: bytes) -> str:
    buf = io.BytesIO(file_bytes)
    doc = Document(buf)
    paras = []
    for p in doc.paragraphs:
        txt = (p.text or "").strip()
        if txt:
            paras.append(txt)
    return "\n".join(paras)

def read_pdf(file_bytes: bytes) -> str:
    text = []
    with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
        for page in pdf.pages:
            try:
                t = page.extract_text() or ""
                if t:
                    text.append(t)
            except Exception:
                pass
    return "\n".join(text)

def pdf_ocr(file_bytes: bytes) -> str:
    if not _HAS_OCR:
        return ""
    pages = convert_from_bytes(file_bytes, fmt="png")
    out = []
    for img in pages:
        txt = pytesseract.image_to_string(img, lang="chi_sim+eng")
        if txt:
            out.append(txt)
    return "\n".join(out)

def parse_resume(uploaded_file, use_ocr: bool) -> Tuple[str, str]:
    """
    返回 (纯文本, 文件类型字符串)
    file_type: 'pdf' / 'docx' / 'txt'
    """
    file_bytes = uploaded_file.read()
    name = uploaded_file.name.lower()
    if name.endswith(".pdf"):
        txt = read_pdf(file_bytes)
        if use_ocr and (not txt or len(txt) < 50):
            txt_ocr = pdf_ocr(file_bytes)
            if txt_ocr and len(txt_ocr) > len(txt):
                txt = txt_ocr
        return txt, "pdf"
    elif name.endswith(".docx"):
        return read_docx(file_bytes), "docx"
    else:
        # 纯文本回退
        try:
            return file_bytes.decode("utf-8", errors="ignore"), "txt"
        except Exception:
            return "", "txt"

# =====================================================================
#                           OpenAI 调用
# =====================================================================

def call_openai(messages, temperature=0.2) -> str:
    client = get_openai_client()
    model = get_model_name()
    resp = client.chat.completions.create(
        model=model,
        messages=messages,
        temperature=temperature,
    )
    return (resp.choices[0].message.content or "").strip()

# =====================================================================
#                           导出
# =====================================================================

def export_docx(text: str, title: Optional[str] = None) -> bytes:
    doc = Document()
    if title:
        doc.add_heading(title, level=1)
    for line in (text or "").splitlines():
        doc.add_paragraph(line)
    out = io.BytesIO()
    doc.save(out)
    out.seek(0)
    return out.getvalue()

def export_pdf_simple(text: str, title: Optional[str] = None) -> bytes:
    """ReportLab 简单排版（若未安装则不使用）。"""
    if not _HAS_PDF:
        return b""
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    width, height = A4
    y = height - 20 * mm
    if title:
        c.setFont("Helvetica-Bold", 14)
        c.drawString(20 * mm, y, title)
        y -= 12 * mm
    c.setFont("Helvetica", 10)
    for line in (text or "").splitlines():
        # 简单换页
        if y < 20 * mm:
            c.showPage()
            y = height - 20 * mm
            c.setFont("Helvetica", 10)
        c.drawString(20 * mm, y, line)
        y -= 6 * mm
    c.save()
    buf.seek(0)
    return buf.getvalue()

# =====================================================================
#                           UI
# =====================================================================

st.set_page_config(page_title="AI 智能简历优化", page_icon="🧠", layout="wide")

st.markdown("## 🧠 AI 智能简历优化")
st.caption("上传简历，AI 将根据 JD 一键优化；可选生成求职信（Cover Letter，语言自动随简历）。")

colL, colR = st.columns([1, 1])

with colL:
    uploaded = st.file_uploader("上传简历（PDF 或 DOCX）", type=["pdf", "docx", "txt"])
with colR:
    jd_text = st.text_area("粘贴目标职位 JD（可批量，用分隔）", height=180, placeholder="贴上 JD 文本……")

st.divider()

# 侧边栏设置
with st.sidebar:
    st.markdown("### 设置")
    refine_pills = st.multiselect("精修侧重", ["业务影响", "沟通协作", "领导力", "技术深度", "数据驱动"], default=["业务影响"])
    st.markdown("**增强点**")
    st.caption("数据驱动、可量化、关键词契合")
    gen_cl = st.checkbox("生成求职信（Cover Letter，自动随简历语言）", value=True)
    use_ocr = st.checkbox("启用 OCR（扫描 PDF）", value=False)
    st.markdown("---")
    st.caption("本应用仅用于演示/样例使用，受版权保护。仅供个人求职使用，禁止未授权复制、爬取或商用。")

# 状态展示
if uploaded:
    resume_text, ftype = parse_resume(uploaded, use_ocr)
    if not resume_text.strip():
        st.warning("未能从文件中解析出文本，请检查文件或打开 OCR 试试。")
else:
    resume_text, ftype = "", ""

# 预览区
if resume_text:
    with st.expander("📄 简历文本预览", expanded=False):
        st.text_area("提取结果（前 3000 字）", resume_text[:3000], height=220)

# 自动语言检测
resume_lang = detect_lang_en_zh(resume_text) if resume_text else "en"
st.session_state["resume_lang"] = resume_lang

with st.expander("🌐 语言自动识别", expanded=True):
    st.markdown(f"检测到当前简历语言：**{'中文' if resume_lang == 'zh' else 'English'}**")
    col1, col2 = st.columns(2)
    if col1.toggle("若识别错误，强制改为中文", value=False, key="force_zh"):
        resume_lang = "zh"
        st.session_state["resume_lang"] = "zh"
    if col2.toggle("若识别错误，强制改为英文", value=False, key="force_en"):
        resume_lang = "en"
        st.session_state["resume_lang"] = "en"

# 生成按钮
btn = st.button("🪄 一键生成", type="primary", use_container_width=True, disabled=(not uploaded))

opt_resume = ""
opt_cl = ""

if btn and uploaded:
    with st.spinner("正在优化简历..."):
        resume_prompt, cl_prompt, system_instruction = get_prompts(resume_lang)

        # 结合侧边栏偏好
        prefs = ", ".join(refine_pills) if refine_pills else "impact & clarity"
        prefer_line = (
            f"\n\nPreference: please emphasize {prefs}." if resume_lang == "en"
            else f"\n\n偏好：请更突出 {prefs}。"
        )

        # 简历优化
        messages = [
            {"role": "system", "content": system_instruction},
            {"role": "user", "content": resume_prompt + prefer_line},
            {"role": "user", "content": f"Resume:\n{resume_text}\n\nTarget JD:\n{jd_text or ''}"}
        ]
        try:
            opt_resume = call_openai(messages, temperature=0.2)
        except Exception as e:
            st.error(f"调用模型失败：{e}")
            opt_resume = ""

    if gen_cl and opt_resume:
        with st.spinner("正在生成求职信..."):
            _, cl_prompt, system_instruction = get_prompts(resume_lang)
            cl_messages = [
                {"role": "system", "content": system_instruction},
                {"role": "user", "content": cl_prompt},
                {"role": "user", "content": f"Resume:\n{opt_resume}\n\nTarget JD:\n{jd_text or ''}"}
            ]
            try:
                opt_cl = call_openai(cl_messages, temperature=0.2)
            except Exception as e:
                st.error(f"生成求职信失败：{e}")
                opt_cl = ""

# 展示结果 & 导出
if opt_resume:
    tabs = ["⭐ 优化后简历"]
    if gen_cl:
        tabs.append("📄 求职信（Cover Letter）")
    tabs.append("📤 导出")
    t0, *rest = st.tabs(tabs)

    with t0:
        st.markdown(opt_resume.replace("\n", "  \n"))

    idx = 1
    if gen_cl:
        with rest[0]:
            st.markdown(opt_cl.replace("\n", "  \n"))
        idx = 1
    else:
        idx = 0

    with rest[idx]:
        # DOCX 导出
        docx_bytes = export_docx(opt_resume, title=None)
        st.download_button(
            "⬇️ 下载简历（DOCX）", data=docx_bytes, file_name="Optimized_Resume.docx",
            mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document", use_container_width=True
        )
        # TXT 备选
        st.download_button(
            "⬇️ 下载简历（TXT）", data=opt_resume.encode("utf-8"), file_name="Optimized_Resume.txt",
            mime="text/plain", use_container_width=True
        )
        # PDF（若可用）
        if _HAS_PDF:
            pdf_bytes = export_pdf_simple(opt_resume, title=None)
            if pdf_bytes:
                st.download_button(
                    "⬇️ 下载简历（PDF）", data=pdf_bytes, file_name="Optimized_Resume.pdf",
                    mime="application/pdf", use_container_width=True
                )

        if gen_cl and opt_cl:
            cl_docx = export_docx(opt_cl, title=None)
            st.download_button(
                "⬇️ 下载求职信（DOCX）", data=cl_docx, file_name="Cover_Letter.docx",
                mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document", use_container_width=True
            )
            st.download_button(
                "⬇️ 下载求职信（TXT）", data=opt_cl.encode("utf-8"), file_name="Cover_Letter.txt",
                mime="text/plain", use_container_width=True
            )
            if _HAS_PDF:
                cl_pdf = export_pdf_simple(opt_cl, title=None)
                if cl_pdf:
                    st.download_button(
                        "⬇️ 下载求职信（PDF）", data=cl_pdf, file_name="Cover_Letter.pdf",
                        mime="application/pdf", use_container_width=True
                    )

# 底部提示
st.caption("如遇输出语言不匹配，请展开“语言自动识别”强制切换后再点一次生成。")