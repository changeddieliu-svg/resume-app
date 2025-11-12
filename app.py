# -*- coding: utf-8 -*-
# AI 智能简历优化（含：左侧精修侧重/增强点/求职信/OCR；上传≤50MB，仅PDF/DOCX；自动识别语言；隐藏“200MB”提示）

import os
import io
import re
import time
import pdfplumber
import streamlit as st
from dotenv import load_dotenv
from docx import Document

# ---- 可选依赖（不安装也能跑） ----
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

# ---- OpenAI 客户端（可选，没有也能跑 Demo）----
load_dotenv()
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
client = None
if OPENAI_API_KEY:
    try:
        from openai import OpenAI
        client = OpenAI(api_key=OPENAI_API_KEY)
    except Exception:
        client = None

# ---- 常量与工具 ----
MAX_SIZE = 50 * 1024 * 1024  # 50MB

def detect_is_cjk(text: str) -> bool:
    """简单中文检测：含中文字符的占比>20%即认为中文。"""
    if not text:
        return False
    cjk_count = len(re.findall(r'[\u4e00-\u9fff]', text))
    return (cjk_count / max(len(text), 1)) > 0.2

def lang_of(text: str) -> str:
    return "zh" if detect_is_cjk(text) else "en"

def read_docx(file_bytes: bytes) -> str:
    doc = Document(io.BytesIO(file_bytes))
    return "\n".join(p.text for p in doc.paragraphs if p.text.strip())

def read_pdf_text(file_bytes: bytes, enable_ocr: bool = False) -> str:
    """优先结构化提取；提取不到再用 OCR（如果安装了且开启）"""
    chunks = []
    with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
        for page in pdf.pages:
            t = page.extract_text() or ""
            if t.strip():
                chunks.append(t)
    text = "\n".join(chunks).strip()

    if not text and enable_ocr and _HAS_OCR:
        try:
            images = convert_from_bytes(file_bytes)
            ocr_text = []
            for img in images:
                ocr_text.append(pytesseract.image_to_string(img))
            text = "\n".join(ocr_text)
        except Exception:
            pass
    return text.strip()

def build_prompt(
    resume_text: str,
    jd_text: str,
    language: str,
    focus_tags: list[str],
    enhancement_notes: str,
    need_cover_letter: bool
) -> str:
    """构建给模型的系统/用户合并提示词（简化成一个大 user 指令，兼容性好）"""
    zh = (language == "zh")
    lines = []
    if zh:
        lines.append("你是一名资深简历顾问，请基于用户上传的简历与目标职位，输出同语言的优化简历（纯文本分段）。")
        lines.append("要求：结构清晰、量化结果、突出关键词、去除冗余；不要使用花哨符号，不要虚构经历。")
        if need_cover_letter:
            lines.append("另外，请生成与职位匹配的英文或中文求职信（根据简历语言自动决定），风格专业简洁。")
    else:
        lines.append("You are a senior resume consultant. Based on the user's resume and target JD, output an improved resume (same language).")
        lines.append("Requirements: clear structure, quantified outcomes, highlight keywords, remove fluff; no fancy symbols; no fabrication.")
        if need_cover_letter:
            lines.append("Also generate a matching cover letter in the same language. Keep it concise and professional.")

    if focus_tags:
        if zh:
            lines.append(f"【精修侧重】请特别突出：{', '.join(focus_tags)}。")
        else:
            lines.append(f"[Refinement Focus] Emphasize: {', '.join(focus_tags)}.")

    if enhancement_notes.strip():
        if zh:
            lines.append(f"【增强点】{enhancement_notes.strip()}")
        else:
            lines.append(f"[Custom Emphasis] {enhancement_notes.strip()}")

    if zh:
        lines.append("\n【用户简历】\n" + resume_text.strip())
        lines.append("\n【目标职位/指令】\n" + (jd_text.strip() if jd_text.strip() else "无"))
        lines.append("\n请先输出《优化后的简历》，若需要再输出《求职信》。")
    else:
        lines.append("\n[User Resume]\n" + resume_text.strip())
        lines.append("\n[Target JD / Instruction]\n" + (jd_text.strip() if jd_text.strip() else "N/A"))
        lines.append("\nFirst output the improved RESUME; then, if needed, output the COVER LETTER.")

    return "\n".join(lines)

def llm_generate(prompt: str, temperature: float = 0.4) -> str:
    """调用 OpenAI；无 key 时进入 Demo。"""
    if client is None:
        # Demo 模式：直接回显结构化示例，保证不报错
        return (
            "【演示模式】\n"
            "RESUME (Sample)\n"
            "- Optimized bullets with quantified outcomes...\n"
            "- Highlighted keywords matched to JD...\n\n"
            "COVER LETTER (Sample)\n"
            "Dear Hiring Manager, ...\n"
        )
    try:
        # gpt-4o-mini 成本低、效果好
        r = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "You are a helpful, concise, and rigorous resume optimizer."},
                {"role": "user", "content": prompt},
            ],
            temperature=temperature,
        )
        return r.choices[0].message.content.strip()
    except Exception as e:
        return f"[LLM Error] {e}"

def to_docx(text: str) -> bytes:
    """简洁写入 docx（不使用粗体/引号 hack，避免你遇到的“引号变粗体”现象）"""
    doc = Document()
    for block in re.split(r"\n\s*\n", text.strip()):
        doc.add_paragraph(block.strip())
    bio = io.BytesIO()
    doc.save(bio)
    return bio.getvalue()

def to_pdf(text: str) -> bytes:
    """简单转 PDF（如果安装了 reportlab），否则返回 None"""
    if not _HAS_PDF:
        return None
    bio = io.BytesIO()
    c = canvas.Canvas(bio, pagesize=A4)
    width, height = A4
    left, top, leading = 40, height - 50, 14

    # 分页打印
    y = top
    for line in text.splitlines():
        # 简单自动换页
        if y < 50:
            c.showPage()
            y = top
        c.drawString(left, y, line[:1000])  # 防止超长
        y -= leading
    c.save()
    return bio.getvalue()

# ---------------- UI ----------------
st.set_page_config(page_title="AI 智能简历优化", page_icon="🧠", layout="wide")

# 隐藏默认“Limit 200MB per file”
st.markdown("""
<style>
div[data-testid="stFileUploader"] small,
div[data-testid="stFileUploader"] div:has(> small),
[data-testid="stFileUploadDropzone"] small,
[data-testid="stFileUploadDropzoneDescription"] {
  display: none !important;
}
section.main > div { padding-top: 1rem; }
</style>
""", unsafe_allow_html=True)

st.title("🧠 AI 智能简历优化")

# 左侧栏
with st.sidebar:
    st.markdown("### 设置")
    # 精修侧重（多选）
    focus = st.multiselect(
        "精修侧重（可多选）",
        options=["业务影响", "沟通协作", "数据驱动", "量化成果", "关键字契合", "项目经验", "领导力", "实习/校招专项"],
        default=["业务影响"]
    )
    # 增强点（自定义）
    notes = st.text_area(
        "增强点（可自定义）",
        placeholder="例如：强调数据分析、量化指标；突出与目标岗位的匹配点；或写作风格要求等…",
        height=100
    )
    need_cl = st.checkbox("生成求职信（Cover Letter）", value=True)
    ocr_on = st.checkbox("启用 OCR（扫描 PDF）", value=False)
    st.caption("仅供个人求职使用，禁止商用与爬取。")

# 上传区 + JD/指令
left, right = st.columns([1, 1])
with left:
    st.subheader("上传简历（PDF 或 DOCX）")
    up = st.file_uploader(
        label="",
        type=["pdf", "docx"],            # 不允许 txt
        accept_multiple_files=False,
        label_visibility="collapsed"
    )
    st.caption("支持 **PDF / DOCX** · 单个文件 **≤ 50MB** · 扫描件可开启 **OCR**")

with right:
    st.subheader("粘贴目标职位 JD 或优化指令（可批量、用分隔）")
    jd_text = st.text_area(
        "例如：Actuarial graduate role at Deloitte. 请重点突出数据分析与建模能力；Cover Letter 更正式。",
        placeholder="JD 或 优化指令；支持多条，用空行分隔。",
        height=180,
        label_visibility="collapsed"
    )

st.markdown("—" * 60)

# 一键生成
btn = st.button("🚀 一键生成", use_container_width=True, type="primary")

# ---- 处理逻辑 ----
if btn:
    if not up:
        st.error("请先上传简历（PDF 或 DOCX）。")
        st.stop()
    if up.size > MAX_SIZE:
        st.error("文件过大，请上传 **≤ 50MB** 的 PDF 或 DOCX。")
        st.stop()

    # 读取文本
    with st.spinner("正在读取简历文本…"):
        ext = (up.name.split(".")[-1] or "").lower()
        raw = up.read()

        if ext == "docx":
            resume_text = read_docx(raw)
        elif ext == "pdf":
            resume_text = read_pdf_text(raw, enable_ocr=ocr_on)
        else:
            st.error("仅支持 PDF 或 DOCX。")
            st.stop()

        if not resume_text.strip():
            st.error("未能读取到有效文本：如果是扫描件，请在左侧启用 OCR 再试。")
            st.stop()

    # 自动识别语言
    language = lang_of(resume_text)
    zh = (language == "zh")
    st.info(f"检测到简历语言：{'中文' if zh else 'English'}。将以同语言输出。")

    # 多 JD 拆分（空行分隔），逐条生成
    jd_blocks = [b.strip() for b in re.split(r"\n\s*\n", jd_text or "") if b.strip()]
    if not jd_blocks:
        jd_blocks = [""]  # 若没JD，也能优化原简历

    results = []
    for idx, jd in enumerate(jd_blocks, start=1):
        with st.spinner(f"正在生成（{idx}/{len(jd_blocks)}）…"):
            prompt = build_prompt(
                resume_text=resume_text,
                jd_text=jd,
                language=language,
                focus_tags=focus,
                enhancement_notes=notes,
                need_cover_letter=need_cl
            )
            llm_out = llm_generate(prompt)

        # 展示与下载
        st.subheader(f"{'第' + str(idx) + '份' if zh else 'Variant ' + str(idx)}")
        st.text_area("生成结果（预览）", llm_out, height=300)

        # 导出 DOCX
        docx_bytes = to_docx(llm_out)
        st.download_button(
            label="⬇️ 下载 DOCX",
            data=docx_bytes,
            file_name=(f"优化简历_{idx}.docx" if zh else f"resume_variant_{idx}.docx"),
            mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            use_container_width=True
        )

        # 可选：导出 PDF（若安装 reportlab）
        pdf_bytes = to_pdf(llm_out)
        if pdf_bytes:
            st.download_button(
                label="⬇️ 下载 PDF（可选）",
                data=pdf_bytes,
                file_name=(f"优化简历_{idx}.pdf" if zh else f"resume_variant_{idx}.pdf"),
                mime="application/pdf",
                use_container_width=True
            )

        st.markdown("---")

st.caption("© 2025 AI Resume Optimizer | 仅供个人求职使用，禁止商用与爬取。")