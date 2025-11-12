# -*- coding: utf-8 -*-
# app.py  覆盖版（紧凑抬头 + 50MB 限制 + 仅 PDF/DOCX + 左侧精修选项 + 语言自动识别）
# 说明：
# 1) 无 OPENAI_API_KEY 时自动进入 Demo 模式，不会报错；
# 2) OCR 开关仅在扫描 PDF 提取不到文本时提示占位（你可接入真实 OCR）；
# 3) PDF 导出如需启用，请安装 reportlab 并取消相关注释（已标注）。

import io
import os
import re
import base64
from datetime import datetime
from typing import Tuple

import streamlit as st

# 可选依赖（PDF/Word 读取）
import pdfplumber
from docx import Document

# ====== 可选：如需导出 PDF，请安装 reportlab 并取消下行注释 ======
# from reportlab.lib.pagesizes import A4
# from reportlab.pdfgen import canvas

# -------------------- 页面基础设置 --------------------
st.set_page_config(page_title="AI 智能简历优化", page_icon="🧠", layout="wide")

# -------------------- 全局样式（紧凑抬头 + 真实 50MB 文案 + 上传提示隐藏） --------------------
st.markdown("""
<style>
/* 顶部栏更紧凑 */
[data-testid="stHeader"]{
  visibility: visible !important;
  height: 2.6rem !important;
  background: transparent !important;
}

/* 主容器更靠上、更紧凑 */
.block-container{
  padding-top: 2.0rem !important;     /* 让标题更靠上 */
  max-width: 1200px !important;
  margin: auto !important;
}

/* 隐藏 fileuploader 默认小字(200MB)，避免与我们的 50MB 说明冲突 */
[data-testid="stFileUploader"] small{
  display:none !important;
}

/* 上传组件整体与下方间距 */
[data-testid="stFileUploader"]{
  margin-bottom: 0.8rem !important;
}

/* 标题间距微调 */
h1, h2, h3{
  margin-top: 0.2rem !important;
  margin-bottom: 0.6rem !important;
}

/* 主要按钮样式 */
button[kind="primary"]{
  font-weight: 600 !important;
  border-radius: 6px !important;
  padding: 0.66rem 0 !important;
  font-size: 1rem !important;
}

/* 次要说明块 */
.tip-box{
  background: rgba(130,130,130,0.08);
  border: 1px dashed rgba(130,130,130,0.35);
  padding: 0.7rem 0.9rem;
  border-radius: 8px;
  font-size: 0.92rem;
  line-height: 1.5;
}
</style>
""", unsafe_allow_html=True)

# -------------------- 工具函数 --------------------
ALLOWED_EXTS = {"pdf", "docx"}
MAX_FILE_MB = 50
MAX_FILE_BYTES = MAX_FILE_MB * 1024 * 1024

def file_too_large(file) -> bool:
    try:
        # Streamlit 上传对象有 size 属性；若无则读 buffer 判断
        size = getattr(file, "size", None)
        if size is None:
            # 回退：把内容读进来
            pos = file.tell()
            data = file.read()
            file.seek(pos)
            size = len(data or b"")
        return size > MAX_FILE_BYTES
    except Exception:
        return False

def read_docx(file) -> str:
    doc = Document(file)
    parts = []
    for p in doc.paragraphs:
        if p.text.strip():
            parts.append(p.text.strip())
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
        # 这里可接入真正 OCR；先占位
        full = "[OCR 占位] 当前为占位文本，扫描件 OCR 暂未接入。\n"
    return full

def detect_language(text: str) -> str:
    """ 简易语言检测：中文/英文 """
    if not text:
        return "auto"
    # 如果中文字数明显多于英文，则判中文
    cjk = re.findall(r'[\u4e00-\u9fa5]', text)
    letters = re.findall(r'[A-Za-z]', text)
    if len(cjk) >= len(letters):
        return "zh"
    return "en"

def build_demo_optimized(resume_text: str, jd_text: str, highlights: list, extra: str, lang: str) -> str:
    """ 无 OpenAI 时的 Demo 文本：结构化 + 高亮关键词占位 """
    bullet = "•" if lang == "en" else "・"
    title = "Optimized Resume (Demo)" if lang == "en" else "优化简历（演示版）"
    sug = "Highlights" if lang == "en" else "亮点聚焦"
    req = "JD / Instruction" if lang == "en" else "目标 JD / 指令"
    out = [f"{title}", "", f"{sug}:"]
    for h in (highlights or []):
        out.append(f"{bullet} {h}")
    if extra.strip():
        out.append(f"{bullet} {extra.strip()}")
    out += ["", f"{req}:", jd_text.strip() or "(无)"]
    out += ["", "—— 以下为原始内容提取 ——", resume_text[:2500]]
    return "\n".join(out).strip()

def build_demo_cover_letter(resume_text: str, jd_text: str, lang: str) -> str:
    if lang == "en":
        return (
            "Cover Letter (Demo)\n\n"
            "Dear Hiring Manager,\n\n"
            "I am writing to express my strong interest in this role. "
            "With hands-on experience in data analysis and problem-solving, "
            "I believe my background aligns well with the JD. "
            "Thank you for your time and consideration.\n\n"
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

def make_docx_bytes(content: str, title: str = "resume") -> bytes:
    doc = Document()
    for line in content.splitlines():
        doc.add_paragraph(line)
    bio = io.BytesIO()
    doc.save(bio)
    return bio.getvalue()

# ====== 可选：如需导出 PDF，请安装 reportlab 并取消注释 ======
# def make_pdf_bytes(content: str, title: str = "resume") -> bytes:
#     bio = io.BytesIO()
#     c = canvas.Canvas(bio, pagesize=A4)
#     width, height = A4
#     x, y = 40, height - 50
#     for line in content.splitlines():
#         c.drawString(x, y, line[:120])
#        y -= 16
#         if y < 50:
#             c.showPage()
#             y = height - 50
#     c.save()
#     return bio.getvalue()

# -------------------- 侧边栏（精修侧重/增强点） --------------------
with st.sidebar:
    st.subheader("设置")
    st.caption("（左侧选项仅影响生成时的强调方向）")

    tags = [
        "业务影响", "量化指标", "数据驱动", "模型能力",
        "沟通协作", "项目管理", "客户导向", "领导力",
        "编程能力", "研究分析"
    ]
    selected_tags = st.multiselect("精修侧重（可多选）", tags, default=["业务影响"], help="用于告诉模型，需要特别强调的维度")

    extra_points = st.text_area(
        "增强点（可自定义）",
        placeholder="例如：强调数据分析/量化成果；突出与目标职位的匹配；或写作风格要求等…",
        height=110
    )

    want_cl = st.checkbox("生成求职信（Cover Letter）", value=True)
    use_ocr = st.checkbox("启用 OCR（扫描 PDF）", value=False)

    st.markdown("---")
    st.caption("仅供个人求职使用，禁止商用与爬取。")

# -------------------- 主体 --------------------
st.markdown("## 🧠 AI 智能简历优化")

col_left, col_right = st.columns([1, 1])

with col_left:
    st.markdown("### 上传简历（PDF 或 DOCX）")
    resume_file = st.file_uploader(
        "Drag and drop file here",
        type=list(ALLOWED_EXTS),
        label_visibility="collapsed",
    )
    st.caption(f"支持 PDF / DOCX · 单文件 ≤ {MAX_FILE_MB}MB · 扫描件可启用 OCR")

with col_right:
    st.markdown("### 粘贴目标职位 JD 或优化指令（可批量、用分隔）")
    jd_text = st.text_area(
        "示例：Actuarial graduate role at Deloitte. 请突出数据分析与建模能力；Cover Letter 更正式。",
        value="",
        placeholder="可以粘贴 JD；也可以直接写优化指令（例如强调哪些技能、写作风格、偏行业等）",
        height=180,
        label_visibility="collapsed"
    )

st.markdown(
    '<div class="tip-box">💡 提示：可在左侧设置“精修侧重/增强点”；若 PDF 为扫描件，可开启 OCR。</div>',
    unsafe_allow_html=True
)
st.write("")
generate_btn = st.button("🚀 一键生成", type="primary", use_container_width=True)

# 结果展示容器
out_box = st.container()

# -------------------- 点击生成 --------------------
if generate_btn:
    if not resume_file:
        st.error("请先上传简历文件（仅支持 PDF/DOCX，≤ 50MB）。")
        st.stop()

    ext = resume_file.name.split(".")[-1].lower()
    if ext not in ALLOWED_EXTS:
        st.error("仅支持 PDF/DOCX 文件。")
        st.stop()

    if file_too_large(resume_file):
        st.error(f"文件过大：当前文件超过 {MAX_FILE_MB}MB 限制。")
        st.stop()

    # 读取文本
    with st.spinner("正在解析简历…"):
        if ext == "docx":
            resume_text = read_docx(resume_file)
        else:
            resume_text = read_pdf(resume_file, use_ocr=use_ocr)

    if not resume_text.strip():
        st.error("未能读取到简历文本内容。如为扫描件，请尝试启用 OCR。")
        st.stop()

    # 自动识别语言
    lang = detect_language(resume_text)

    # ===== 如需接入 OpenAI/自研模型，请在此处替换为你的真实生成逻辑 =====
    # 读取 secrets 中的 key：st.secrets.get("OPENAI_API_KEY")
    api_key = st.secrets.get("OPENAI_API_KEY", "")
    in_demo = False
    if not api_key:
        in_demo = True

    # 组装“精修侧重”
    highlight_texts = selected_tags or []
    extra_text = extra_points or ""

    with st.spinner("正在生成优化简历…"):
        if in_demo:
            optimized_resume = build_demo_optimized(
                resume_text=resume_text,
                jd_text=jd_text,
                highlights=highlight_texts,
                extra=extra_text,
                lang=lang
            )
        else:
            # ======= 这里替换成你的真实模型调用 =======
            # optimized_resume = your_model_generate(resume_text, jd_text, selected_tags, extra_points, lang)
            optimized_resume = build_demo_optimized(
                resume_text=resume_text,
                jd_text=jd_text,
                highlights=highlight_texts,
                extra=extra_text,
                lang=lang
            )

    with out_box:
        st.subheader("✅ 优化简历预览")
        st.text_area("（可复制粘贴到 Word）", optimized_resume, height=300, label_visibility="collapsed")

        # 导出 DOCX
        docx_bytes = make_docx_bytes(optimized_resume, "optimized_resume")
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        st.download_button(
            "⬇️ 下载 DOCX",
            data=docx_bytes,
            file_name=f"Optimized_Resume_{ts}.docx",
            mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            use_container_width=True
        )

        # ====== 如需导出 PDF，取消下方注释（并确认安装 reportlab） ======
        # pdf_bytes = make_pdf_bytes(optimized_resume, "optimized_resume")
        # st.download_button(
        #     "⬇️ 下载 PDF",
        #     data=pdf_bytes,
        #     file_name=f"Optimized_Resume_{ts}.pdf",
        #     mime="application/pdf",
        #     use_container_width=True
        # )

    # 求职信（可选）
    if want_cl:
        with st.spinner("正在生成求职信…"):
            if in_demo:
                cover_letter = build_demo_cover_letter(resume_text, jd_text, lang)
            else:
                # ======= 这里替换成你的真实模型调用 =======
                # cover_letter = your_model_generate_cover_letter(resume_text, jd_text, lang)
                cover_letter = build_demo_cover_letter(resume_text, jd_text, lang)

        st.subheader("📄 求职信（可选）")
        st.text_area("（可复制粘贴到 Word）", cover_letter, height=240, label_visibility="collapsed")

        cl_docx = make_docx_bytes(cover_letter, "cover_letter")
        st.download_button(
            "⬇️ 下载求职信 DOCX",
            data=cl_docx,
            file_name=f"Cover_Letter_{ts}.docx",
            mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            use_container_width=True
        )
        # ====== 如需导出 PDF，取消下方注释（并确认安装 reportlab） ======
        # cl_pdf = make_pdf_bytes(cover_letter, "cover_letter")
        # st.download_button(
        #     "⬇️ 下载求职信 PDF",
        #     data=cl_pdf,
        #     file_name=f"Cover_Letter_{ts}.pdf",
        #     mime="application/pdf",
        #     use_container_width=True
        # )

# -------------------- 页脚 --------------------
st.write("")
st.write("---")
st.caption("© 2025 AI Resume Optimizer | 仅供个人求职使用，禁止商用与爬取。")