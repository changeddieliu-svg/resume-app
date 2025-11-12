# -*- coding: utf-8 -*-
# AI 智能简历优化 - 终极版（标题修复 + 左侧栏完整 + 上传≤50MB + 自动语言识别）

import os, io, re, pdfplumber, streamlit as st
from dotenv import load_dotenv
from docx import Document

# 可选依赖（OCR、PDF导出）
_HAS_OCR = True
try:
    from pdf2image import convert_from_bytes
    import pytesseract
except Exception:
    _HAS_OCR = False

_HAS_PDF = True
try:
    from reportlab.pdfgen import canvas
    from reportlab.lib.pagesizes import A4
except Exception:
    _HAS_PDF = False

# OpenAI 客户端（可选）
load_dotenv()
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
client = None
if OPENAI_API_KEY:
    try:
        from openai import OpenAI
        client = OpenAI(api_key=OPENAI_API_KEY)
    except Exception:
        client = None

MAX_SIZE = 50 * 1024 * 1024  # 上传上限 50MB


# -------- 工具函数 --------
def detect_is_cjk(text: str) -> bool:
    """检测是否中文"""
    if not text:
        return False
    cjk_count = len(re.findall(r"[\u4e00-\u9fff]", text))
    return (cjk_count / max(len(text), 1)) > 0.2


def lang_of(text: str) -> str:
    return "zh" if detect_is_cjk(text) else "en"


def read_docx(file_bytes: bytes) -> str:
    doc = Document(io.BytesIO(file_bytes))
    return "\n".join(p.text for p in doc.paragraphs if p.text.strip())


def read_pdf_text(file_bytes: bytes, enable_ocr=False) -> str:
    text = ""
    with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
        for p in pdf.pages:
            txt = p.extract_text() or ""
            text += txt + "\n"
    if not text.strip() and enable_ocr and _HAS_OCR:
        try:
            imgs = convert_from_bytes(file_bytes)
            for img in imgs:
                text += pytesseract.image_to_string(img)
        except Exception:
            pass
    return text.strip()


def build_prompt(resume_text, jd_text, lang, focus_tags, notes, need_cover):
    zh = lang == "zh"
    lines = []
    if zh:
        lines.append("你是一名资深简历顾问，请优化下列简历，使其更符合目标职位。输出中文优化简历，可选生成求职信。")
    else:
        lines.append("You are a professional resume consultant. Improve the resume to match the JD, output in the same language.")
    if focus_tags:
        lines.append(("精修侧重：" if zh else "Focus: ") + ", ".join(focus_tags))
    if notes.strip():
        lines.append(("增强点：" if zh else "Additional notes: ") + notes.strip())
    if need_cover:
        lines.append("并生成一份求职信。")
    lines.append(("\n【原始简历】\n" if zh else "\n[Resume]\n") + resume_text.strip())
    if jd_text.strip():
        lines.append(("\n【目标职位】\n" if zh else "\n[Target JD]\n") + jd_text.strip())
    return "\n".join(lines)


def llm_generate(prompt: str):
    if not client:
        return (
            "【演示模式】\n示例优化简历段落：\n- 优化后的要点展示...\n\nCOVER LETTER 示例：\n尊敬的招聘经理..."
        )
    try:
        r = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "You are a helpful, concise resume optimizer."},
                {"role": "user", "content": prompt},
            ],
            temperature=0.4,
        )
        return r.choices[0].message.content.strip()
    except Exception as e:
        return f"[LLM Error] {e}"


def to_docx(text: str) -> bytes:
    doc = Document()
    for block in re.split(r"\n\s*\n", text.strip()):
        doc.add_paragraph(block.strip())
    bio = io.BytesIO()
    doc.save(bio)
    return bio.getvalue()


def to_pdf(text: str) -> bytes:
    if not _HAS_PDF:
        return None
    bio = io.BytesIO()
    c = canvas.Canvas(bio, pagesize=A4)
    w, h = A4
    y = h - 50
    for line in text.splitlines():
        if y < 50:
            c.showPage()
            y = h - 50
        c.drawString(40, y, line[:1200])
        y -= 14
    c.save()
    return bio.getvalue()


# -------- Streamlit UI --------
st.set_page_config(page_title="AI 智能简历优化", page_icon="🧠", layout="wide")

# ---------- 样式修复（标题不遮挡 + 美化留白） ----------
st.markdown("""
<style>
[data-testid="stHeader"] {
  visibility: visible !important;
  height: 3.5rem !important;
  background: transparent !important;
}
[data-testid="stToolbar"] {
  visibility: hidden !important;
  height: 0 !important;
}
.block-container {
  padding-top: 5rem !important;
  max-width: 1200px !important;
  margin: auto !important;
}
h1, h2, h3 { margin-top: 0.5rem !important; }
[data-testid="stFileUploader"] small { display: none !important; }
</style>
""", unsafe_allow_html=True)

# ---------- 页面 ----------
st.title("🧠 AI 智能简历优化")

with st.sidebar:
    st.markdown("### 设置")
    focus = st.multiselect(
        "精修侧重（可多选）",
        ["业务影响", "沟通协作", "数据驱动", "量化成果", "关键字契合", "项目经验", "领导力", "实习/校招专项"],
        ["业务影响"],
    )
    notes = st.text_area("增强点（可自定义）", placeholder="如：突出分析能力、强调领导力、语气更正式等")
    need_cl = st.checkbox("生成求职信（Cover Letter）", value=True)
    ocr_on = st.checkbox("启用 OCR（扫描PDF）", value=False)
    st.caption("仅供个人求职使用，禁止商用与爬取。")

# 上传与JD输入
col1, col2 = st.columns([1, 1])
with col1:
    st.subheader("上传简历（PDF 或 DOCX）")
    up = st.file_uploader("", type=["pdf", "docx"], label_visibility="collapsed")
    st.caption("支持 PDF / DOCX · ≤50MB · 扫描件可启用 OCR")

with col2:
    st.subheader("粘贴目标职位 JD 或优化指令（可批量、用分隔）")
    jd_text = st.text_area(
        "JD 或优化指令",
        placeholder="如：Actuarial graduate role at Deloitte. 请突出数据分析与建模能力；Cover Letter 要更正式。",
        height=180,
        label_visibility="collapsed",
    )

st.markdown("---")

# ---------- 主逻辑 ----------
if st.button("🚀 一键生成", use_container_width=True, type="primary"):
    if not up:
        st.error("请先上传简历文件。")
        st.stop()
    if up.size > MAX_SIZE:
        st.error("文件过大，请上传 ≤50MB 的 PDF 或 DOCX 文件。")
        st.stop()

    ext = (up.name.split(".")[-1] or "").lower()
    raw = up.read()

    with st.spinner("正在解析简历..."):
        if ext == "pdf":
            resume_text = read_pdf_text(raw, enable_ocr=ocr_on)
        elif ext == "docx":
            resume_text = read_docx(raw)
        else:
            st.error("仅支持 PDF / DOCX 文件。")
            st.stop()

    if not resume_text.strip():
        st.error("未识别到有效文本。若为扫描件，请启用 OCR 再试。")
        st.stop()

    lang = lang_of(resume_text)
    zh = lang == "zh"
    st.info(f"检测到简历语言：{'中文' if zh else 'English'}。将以同语言输出。")

    jd_blocks = [b.strip() for b in re.split(r"\n\s*\n", jd_text or "") if b.strip()] or [""]

    for idx, jd in enumerate(jd_blocks, start=1):
        with st.spinner(f"正在生成第 {idx} 份..."):
            prompt = build_prompt(resume_text, jd, lang, focus, notes, need_cl)
            result = llm_generate(prompt)

        st.subheader(f"{'第' + str(idx) + '份结果' if zh else 'Variant ' + str(idx)}")
        st.text_area("结果预览", result, height=300)

        docx_bytes = to_docx(result)
        st.download_button(
            "⬇️ 下载 DOCX",
            data=docx_bytes,
            file_name=f"优化简历_{idx}.docx" if zh else f"resume_variant_{idx}.docx",
            mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            use_container_width=True,
        )

        pdf_bytes = to_pdf(result)
        if pdf_bytes:
            st.download_button(
                "⬇️ 下载 PDF（可选）",
                data=pdf_bytes,
                file_name=f"优化简历_{idx}.pdf" if zh else f"resume_variant_{idx}.pdf",
                mime="application/pdf",
                use_container_width=True,
            )

st.caption("© 2025 AI Resume Optimizer | 仅供个人求职使用，禁止商用与爬取。")