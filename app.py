# -*- coding: utf-8 -*-
# AI 智能简历优化 — 完整覆盖版（2025最新版）

import io, os, re
from datetime import datetime
from typing import List
import streamlit as st
import pdfplumber
from docx import Document

# ========== 页面配置 ==========
st.set_page_config(
    page_title="AI 智能简历优化",
    page_icon="🧠",
    layout="wide",
    initial_sidebar_state="expanded",
    menu_items={  # 关闭右上角默认菜单项
        "Get Help": None,
        "Report a bug": None,
        "About": None,
    },
)

# ========== 页面样式（顶部齐平 + 隐藏菜单） ==========
st.markdown("""
<style>

/* 隐藏右上角菜单/分享按钮 */
header [data-testid="stToolbar"],
header [data-testid="stActionButtonIcon"],
header [data-testid="stDeployButton"],
header [data-testid="baseButton-headerNoPadding"],
header .stAppHeaderRight {
  display: none !important;
}

/* 顶部留白压缩，让标题与左栏齐平 */
[data-testid="stHeader"] {
  visibility: visible !important;
  height: 2.4rem !important;         /* 顶部高度 */
  background: transparent !important;
}

.block-container {
  padding-top: 0.6rem !important;    /* 主区域上移 */
  max-width: 1200px !important;
  margin: auto !important;
}

/* 上传控件样式 */
[data-testid="stFileUploader"] small { display: none !important; } /* 隐藏默认200MB提示 */
[data-testid="stFileUploader"] { margin-bottom: 0.6rem !important; }

/* 标题与控件间距 */
h1, h2, h3 { margin-top: 0.15rem !important; margin-bottom: 0.4rem !important; }

/* 主按钮样式 */
button[kind="primary"] {
  font-weight: 600 !important;
  border-radius: 6px !important;
  padding: 0.6rem 0 !important;
  font-size: 1rem !important;
}

/* 提示框样式 */
.tip-box {
  background: rgba(130,130,130,0.08);
  border: 1px dashed rgba(130,130,130,0.35);
  padding: 0.7rem 0.9rem;
  border-radius: 8px;
  font-size: 0.92rem;
  line-height: 1.5;
}

/* 隐藏Streamlit底部装饰(可选) */
/* [data-testid="stDecoration"] { display:none !important; } */

</style>
""", unsafe_allow_html=True)


# ========== 工具函数 ==========
ALLOWED_EXTS = {"pdf", "docx"}
MAX_FILE_MB = 50
MAX_FILE_BYTES = MAX_FILE_MB * 1024 * 1024

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
    out += ["", f"{req}:", jd_text.strip() or "(无)"]
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


# ========== 左侧栏 ==========
with st.sidebar:
    st.subheader("设置")
    st.caption("（左侧选项仅影响生成时的强调方向）")

    tags = ["业务影响", "量化指标", "数据驱动", "模型能力", "沟通协作", "项目管理", "客户导向", "领导力", "编程能力", "研究分析"]
    selected_tags = st.multiselect("精修侧重（可多选）", tags, default=["业务影响"])
    extra_points = st.text_area("增强点（可自定义）", placeholder="例如：强调数据分析/量化成果；突出与目标职位的匹配；或写作风格要求等…", height=110)
    want_cl = st.checkbox("生成求职信（Cover Letter）", value=True)
    use_ocr = st.checkbox("启用 OCR（扫描PDF）", value=False)
    st.markdown("---")
    st.caption("仅供个人求职使用，禁止商用与爬取。")

# ========== 主体 ==========
st.markdown("## 🧠 AI 智能简历优化")

col_left, col_right = st.columns([1, 1])

with col_left:
    st.markdown("### 上传简历（PDF 或 DOCX）")
    resume_file = st.file_uploader("上传文件", type=list(ALLOWED_EXTS), label_visibility="collapsed")
    st.caption(f"支持 PDF / DOCX · 单文件 ≤ {MAX_FILE_MB}MB · 扫描件可启用 OCR")

with col_right:
    st.markdown("### 粘贴目标职位 JD 或优化指令（可批量、用分隔）")
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

# ========== 生成逻辑 ==========
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

    with st.spinner("正在解析简历…"):
        resume_text = read_docx(resume_file) if ext == "docx" else read_pdf(resume_file, use_ocr)
    if not resume_text.strip():
        st.error("未能读取到简历内容。如为扫描件，请尝试启用 OCR。")
        st.stop()

    lang = detect_language(resume_text)
    api_key = st.secrets.get("OPENAI_API_KEY", "")
    in_demo = not bool(api_key)

    with st.spinner("正在生成优化简历…"):
        optimized_resume = build_demo_optimized(resume_text, jd_text, selected_tags, extra_points, lang)

    with out_box:
        st.subheader("✅ 优化简历预览")
        st.text_area("", optimized_resume, height=300, label_visibility="collapsed")

        docx_bytes = make_docx_bytes(optimized_resume, "optimized_resume")
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        st.download_button("⬇️ 下载 DOCX", docx_bytes, file_name=f"Optimized_Resume_{ts}.docx",
                           mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                           use_container_width=True)

    if want_cl:
        with st.spinner("正在生成求职信…"):
            cover_letter = build_demo_cover_letter(resume_text, jd_text, lang)

        st.subheader("📄 求职信（可选）")
        st.text_area("", cover_letter, height=240, label_visibility="collapsed")

        cl_docx = make_docx_bytes(cover_letter, "cover_letter")
        st.download_button("⬇️ 下载求职信 DOCX", cl_docx,
                           file_name=f"Cover_Letter_{ts}.docx",
                           mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                           use_container_width=True)

st.write("")
st.write("---")
st.caption("© 2025 AI Resume Optimizer | 仅供个人求职使用，禁止商用与爬取。")