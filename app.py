# -*- coding: utf-8 -*-
# AI 智能简历优化（≤50MB、仅 PDF/DOCX、隐藏 200MB 提示、自动语言、可选 Cover Letter）

import os
import io
import re
import time
import pdfplumber
import streamlit as st
from dotenv import load_dotenv
from docx import Document

# ==== 可选：PDF 导出（安装 reportlab 才可用） ====
_HAS_REPORTLAB = True
try:
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas
except Exception:
    _HAS_REPORTLAB = False

# ==== 可选：OCR（针对图片/扫描 PDF） ====
_HAS_OCR = True
try:
    from pdf2image import convert_from_bytes
    import pytesseract
except Exception:
    _HAS_OCR = False

# ==== OpenAI v1 客户端 ====
load_dotenv()
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
client = None
if OPENAI_API_KEY:
    try:
        from openai import OpenAI
        client = OpenAI(api_key=OPENAI_API_KEY)
    except Exception:
        client = None

# ==== 页面配置 ====
st.set_page_config(
    page_title="AI 智能简历优化",
    page_icon="🧠",
    layout="wide"
)

# ==== 全局样式（隐藏 200MB 行；适配深色；按钮与卡片样式） ====
st.markdown("""
<style>
[data-testid="stFileUploadDropzone"] small {display: none !important;}
[data-testid="stFileUploadDropzone"] p {display: none !important;} /* 兜底隐藏 */
section.main > div {padding-top: 1rem;}
.stDownloadButton > button {width: 100%;}
</style>
""", unsafe_allow_html=True)

# ==== 小工具 ====
MAX_SIZE = 50 * 1024 * 1024  # 50MB

def is_cjk_text(s: str, ratio_threshold: float = 0.2) -> bool:
    """简单中文检测：CJK 字符占比 > 20% 判为中文"""
    if not s:
        return False
    cjk = len(re.findall(r'[\u4e00-\u9fff]', s))
    return cjk / max(len(s), 1) >= ratio_threshold

def read_docx(file_bytes: bytes) -> str:
    bio = io.BytesIO(file_bytes)
    doc = Document(bio)
    return "\n".join([p.text for p in doc.paragraphs])

def read_pdf_text(file_bytes: bytes, ocr: bool = False) -> str:
    """优先文本抽取；若几乎无文本且开启 OCR，则用 OCR 识别"""
    text_segments = []
    with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
        for page in pdf.pages:
            t = page.extract_text() or ""
            text_segments.append(t)
    raw = "\n".join(text_segments).strip()

    if raw and len(re.sub(r"\s+", "", raw)) > 50:
        return raw

    if ocr and _HAS_OCR:
        images = convert_from_bytes(file_bytes)
        ocr_text = []
        for im in images:
            ocr_text.append(pytesseract.image_to_string(im))
        return "\n".join(ocr_text).strip()
    return raw  # 可能为空（无文本且未开 OCR）

def improve_with_openai(resume_text: str, jd_text: str, lang: str, want_cover_letter: bool) -> dict:
    """
    调用 OpenAI 生成优化简历与可选求职信。
    返回 {"resume": "...", "cover_letter": "...或空"}。
    """
    if not client:
        # 演示占位内容（保障 UI 有输出）
        if lang == "zh":
            return {
                "resume": "【演示模式】这是根据你的中文简历与 JD 指令生成的优化稿。\n\n- 用数字量化成果\n- 强调与 JD 匹配的关键词\n- 保持结构清晰（教育/经历/技能）\n\n（请配置 OPENAI_API_KEY 以启用真实优化）",
                "cover_letter": "【演示模式】中文求职信范例：\n尊敬的招聘经理...\n（请配置 OPENAI_API_KEY 以启用真实生成）" if want_cover_letter else ""
            }
        else:
            return {
                "resume": "[DEMO] Optimized resume draft in English.\n\n- Quantify achievements\n- Highlight JD keywords\n- Keep structure clear (Education/Experience/Skills)\n\n(Configure OPENAI_API_KEY to enable real generation.)",
                "cover_letter": "[DEMO] Cover letter sample in English...\n(Configure OPENAI_API_KEY to enable real generation.)" if want_cover_letter else ""
            }

    system_zh = (
        "你是一名资深招聘顾问和简历优化专家。请使用**简历原语言**写作。"
        "目标：在不过度夸张的前提下，提升与 JD 的匹配度、量化成果、优化结构与措辞，并保留真实信息。"
        "输出顺序：先给“优化后的简历（纯文本）”，若用户需要，再给“求职信”。不要输出多余解释。"
    )
    system_en = (
        "You are a senior resume optimizer and career consultant. "
        "Write in the **same language as the original resume**. "
        "Goals: improve JD alignment, quantify achievements, polish style and structure without exaggeration. "
        "Output order: first the 'Optimized Resume (plain text)', then the 'Cover Letter' only if requested. "
        "Do not include explanations."
    )
    system_msg = system_zh if lang == "zh" else system_en

    cover_hint = ("请在最后补充一份正式语气的求职信。" if lang == "zh"
                  else "At the end, also include a formal cover letter.") if want_cover_letter else ""

    user_prompt = (
        f"【候选人原始简历】\n{resume_text}\n\n"
        f"【目标职位JD/优化指令】\n{jd_text}\n\n"
        f"{cover_hint}\n"
        f"请确保简历结构清晰（教育/项目/实习/经历/技能），关键结果尽量可量化。"
        if lang == "zh" else
        f"[Original Resume]\n{resume_text}\n\n"
        f"[Target JD / Instructions]\n{jd_text}\n\n"
        f"{cover_hint}\n"
        f"Keep the resume well-structured (Education/Projects/Experience/Skills) "
        f"and quantify results whenever possible."
    )

    try:
        rsp = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": system_msg},
                {"role": "user", "content": user_prompt}
            ],
            temperature=0.3,
        )
        full = rsp.choices[0].message.content.strip()

        # 粗分：若生成了 Cover Letter，则猜一个分隔（更可靠可用 Markdown 标题等规则）
        resume_out, cover_out = full, ""
        m = re.search(r"(cover letter|求职信)", full, re.I)
        if want_cover_letter and m:
            idx = m.start()
            resume_out = full[:idx].strip()
            cover_out = full[idx:].strip()

        return {"resume": resume_out, "cover_letter": cover_out if want_cover_letter else ""}
    except Exception as e:
        msg = f"模型调用失败：{e}"
        if lang == "zh":
            return {"resume": f"【出错提示】{msg}", "cover_letter": ""}
        return {"resume": f"[Error] {msg}", "cover_letter": ""}

def make_docx(text: str) -> bytes:
    doc = Document()
    for line in text.splitlines():
        doc.add_paragraph(line)
    bio = io.BytesIO()
    doc.save(bio)
    return bio.getvalue()

def make_pdf(text: str) -> bytes:
    if not _HAS_REPORTLAB:
        return b""
    bio = io.BytesIO()
    c = canvas.Canvas(bio, pagesize=A4)
    width, height = A4
    left, top = 50, height - 50
    y = top
    for raw_line in text.splitlines():
        line = raw_line.replace("\t", "    ")
        # 简单换行（粗糙处理）
        max_chars = 95
        chunks = [line[i:i+max_chars] for i in range(0, len(line), max_chars)] or [""]
        for seg in chunks:
            c.drawString(left, y, seg)
            y -= 14
            if y < 60:
                c.showPage()
                y = top
    c.showPage()
    c.save()
    return bio.getvalue()

# ==== 侧边栏 ====
with st.sidebar:
    st.subheader("设置")
    want_cover = st.checkbox("生成求职信（Cover Letter）", value=True)
    use_ocr = st.checkbox("启用 OCR（扫描 PDF）", value=False, disabled=not _HAS_OCR)
    if use_ocr and not _HAS_OCR:
        st.info("当前环境未安装 OCR 依赖（pdf2image / pytesseract），将自动忽略。")

# ==== 页面主体 ====
st.title("🧠 AI 智能简历优化")
st.caption("上传简历，粘贴 JD 或优化指令，一键生成优化版（支持自动匹配语言，可选生成 Cover Letter）。")

col1, col2 = st.columns([1.05, 1])
with col1:
    uploaded = st.file_uploader(
        "上传简历（PDF 或 DOCX）",
        type=["pdf", "docx"],  # 明确禁止 txt
        accept_multiple_files=False,
        help="单文件 ≤ 50MB；仅支持 PDF / DOCX。若 PDF 为扫描件，可在左侧启用 OCR。"
    )
    st.caption("单文件 ≤ 50MB · 仅支持 PDF / DOCX")

with col2:
    jd_or_instr = st.text_area(
        "粘贴目标职位 JD 或优化指令（可批量、用分隔）",
        placeholder="例如：Actuarial graduate role at Deloitte. 请重点突出数据分析与建模能力；Cover Letter 更正式。",
        height=180
    )

st.markdown(
    "> 💡 提示：可在右侧输入框写“请突出某技能、指定行业、写法”等优化要求。"
)

gen_btn = st.button("🚀 一键生成", use_container_width=True)

# ==== 处理逻辑 ====
if gen_btn:
    if not uploaded:
        st.error("请先上传简历文件（PDF / DOCX）。")
        st.stop()

    # 文件大小与类型校验
    if uploaded.size is None or uploaded.size > MAX_SIZE:
        st.error("文件过大：请上传 ≤ 50MB 的简历文件。")
        st.stop()

    filename = (uploaded.name or "").lower()
    if not (filename.endswith(".pdf") or filename.endswith(".docx")):
        st.error("仅支持 PDF / DOCX。")
        st.stop()

    raw_text = ""
    with st.spinner("解析简历中…"):
        if filename.endswith(".docx"):
            try:
                raw_text = read_docx(uploaded.getvalue())
            except Exception as e:
                st.error(f"DOCX 解析失败：{e}")
                st.stop()
        else:
            try:
                raw_text = read_pdf_text(uploaded.getvalue(), ocr=use_ocr)
            except Exception as e:
                st.error(f"PDF 解析失败：{e}")
                st.stop()

    if not raw_text or len(raw_text.strip()) < 20:
        st.error("未能提取到有效文本（若为扫描 PDF，请尝试启用 OCR）。")
        st.stop()

    # 自动语言
    lang = "zh" if is_cjk_text(raw_text) else "en"
    st.info(("自动识别语言：中文" if lang == "zh" else "Auto-detected language: English"))

    # 调用模型优化
    with st.spinner("AI 正在为你优化简历…"):
        out = improve_with_openai(raw_text, jd_or_instr or "", lang, want_cover)
        resume_out = out.get("resume", "").strip()
        cover_out  = out.get("cover_letter", "").strip()

    # 展示结果
    st.subheader("✅ 优化结果")
    st.write(resume_out)

    if want_cover:
        st.markdown("---")
        st.subheader("📄 求职信（Cover Letter）")
        if cover_out:
            st.write(cover_out)
        else:
            st.info("未生成求职信或为空。")

    # 导出
    st.markdown("---")
    st.subheader("⬇️ 导出")

    # DOCX
    docx_bytes = make_docx(resume_out)
    st.download_button(
        "下载简历（DOCX）",
        data=docx_bytes,
        file_name="Optimized_Resume.docx",
        mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    )

    # PDF（若未安装 reportlab 则禁用）
    if _HAS_REPORTLAB:
        pdf_bytes = make_pdf(resume_out)
        st.download_button(
            "下载简历（PDF）",
            data=pdf_bytes,
            file_name="Optimized_Resume.pdf",
            mime="application/pdf"
        )
    else:
        st.caption("如需导出 PDF，请在环境中安装 reportlab：`pip install reportlab`")

# 页脚
st.markdown("---")
st.caption("© 2025 AI Resume Optimizer｜仅供个人求职使用，禁止商用与爬取。")