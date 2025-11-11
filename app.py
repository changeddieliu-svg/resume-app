import os
import io
import re
from typing import Tuple

import streamlit as st
from dotenv import load_dotenv
from docx import Document
from openai import OpenAI

# ============= 可选依赖（云端可能缺） =============
# pdfplumber 常见且轻量；云端通常可用
try:
    import pdfplumber
except Exception:
    pdfplumber = None

# OCR 依赖（云端未必装好，运行时再判断）
def _safe_import_ocr():
    try:
        from pdf2image import convert_from_bytes
        import pytesseract
        return convert_from_bytes, pytesseract
    except Exception:
        return None, None

# ============= 页面配置 & 样式修复（防标题遮挡） =============
st.set_page_config(page_title="AI 智能简历优化", page_icon="🧠", layout="wide")
st.markdown("""
<style>
/* 保留 Header 高度，避免内容被顶上去 */
[data-testid="stHeader"]{visibility:visible;height:2.8rem;background:transparent;}
[data-testid="stToolbar"]{visibility:hidden;height:2.8rem;}
.block-container{padding-top:3.2rem!important;max-width:1200px;}
h1:first-child,.stMarkdown h1:first-child{margin-top:0.6rem!important;}
button[kind="primary"] { font-weight: 600; }
</style>
""", unsafe_allow_html=True)

# ============= 加载密钥 & 初始化 OpenAI =============
load_dotenv()
OPENAI_API_KEY = st.secrets.get("OPENAI_API_KEY", os.getenv("OPENAI_API_KEY", ""))
MODEL_NAME = st.secrets.get("MODEL_NAME", os.getenv("MODEL_NAME", "gpt-4o-mini"))

if not OPENAI_API_KEY:
    st.error("⚠️ 未检测到 OPENAI_API_KEY。请在 Streamlit → Settings → Secrets 添加：OPENAI_API_KEY = \"sk-...\"")
    client = None
else:
    client = OpenAI(api_key=OPENAI_API_KEY)

# ============= 工具函数 =============
def detect_language(text: str) -> str:
    """简单检测：中文多则 zh，否则 en"""
    zh = len(re.findall(r'[\u4e00-\u9fff]', text or ""))
    en = len(re.findall(r'[A-Za-z]', text or ""))
    return "zh" if zh > en else "en"

def _read_pdf_text(file_bytes: bytes) -> str:
    """优先使用 pdfplumber 提取文本"""
    if not pdfplumber:
        return ""
    text = []
    try:
        with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
            for p in pdf.pages:
                text.append(p.extract_text() or "")
    except Exception:
        return ""
    return "\n".join(text).strip()

def _ocr_pdf(file_bytes: bytes) -> str:
    """OCR 识别扫描 PDF（若依赖缺失则返回空）"""
    convert_from_bytes, pytesseract = _safe_import_ocr()
    if not (convert_from_bytes and pytesseract):
        return ""
    try:
        images = convert_from_bytes(file_bytes, dpi=300)
        parts = [pytesseract.image_to_string(im, lang="chi_sim+eng") for im in images]
        return "\n".join(parts).strip()
    except Exception:
        return ""

def read_resume(uploaded_file, use_ocr: bool) -> Tuple[str, str]:
    """
    读取 PDF/DOCX 文本；不支持 TXT。
    - PDF：pdfplumber；若文本极少且 use_ocr=True，尝试 OCR
    - DOCX：python-docx
    返回 (文本, 格式名)
    """
    name = uploaded_file.name.lower()

    uploaded_file.seek(0)
    raw_bytes = uploaded_file.read()
    uploaded_file.seek(0)

    if name.endswith(".pdf"):
        text = _read_pdf_text(raw_bytes)
        # 文本极少时尝试 OCR（可选）
        if len(text) < 20 and use_ocr:
            ocr_text = _ocr_pdf(raw_bytes)
            if ocr_text:
                return ocr_text, "PDF(OCR)"
        if not text:
            raise ValueError("未能从 PDF 中提取到文本。若为扫描件，请开启 OCR 或更换更清晰的文件。")
        return text, "PDF"

    elif name.endswith(".docx"):
        doc = Document(io.BytesIO(raw_bytes))
        text = "\n".join(p.text for p in doc.paragraphs if p.text).strip()
        if not text:
            raise ValueError("DOCX 内容为空，请检查文件。")
        return text, "DOCX"

    # 明确拒绝 TXT/其他格式
    raise ValueError("当前版本仅支持 PDF 或 DOCX。")

def build_focus_instructions(focus_tags, custom_points, lang):
    """根据侧栏选项生成优化指令片段"""
    if not focus_tags and not custom_points:
        return ""
    if lang == "zh":
        parts = []
        if focus_tags:
            parts.append("请在优化中特别强调以下侧重点：" + "、".join(focus_tags) + "。")
        if custom_points:
            parts.append("其他自定义要求：" + custom_points.strip())
        return "\n".join(parts)
    else:
        parts = []
        if focus_tags:
            parts.append("Please emphasise the following focus areas in the optimisation: " +
                         ", ".join(focus_tags) + ".")
        if custom_points:
            parts.append("Additional user notes: " + custom_points.strip())
        return "\n".join(parts)

def llm_optimize_resume(resume_text: str, jd_text: str, lang: str, focus_directives: str) -> str:
    """调用模型生成优化简历"""
    if not client:
        raise RuntimeError("OpenAI client 未初始化。请配置 OPENAI_API_KEY。")
    prompt = f"""
You are a professional career consultant AI.
Optimise the following resume to better match the job description / user instructions.
Keep the same language as the resume: {"Chinese (简体中文)" if lang=="zh" else "English"}.
Focus on quantifiable achievements, clear structure, strong action verbs, ATS-friendly formatting.

[Job description or user instructions]
{jd_text or "(none)"}

[User focus directives]
{focus_directives or "(none)"}

[Original resume]
{resume_text}

[Output requirement]
Return ONLY the optimised resume text (no extra commentary).
"""
    rsp = client.chat.completions.create(
        model=MODEL_NAME,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.6,
    )
    return (rsp.choices[0].message.content or "").strip()

def llm_cover_letter(resume_text: str, jd_text: str, lang: str, focus_directives: str) -> str:
    """调用模型生成求职信"""
    if not client:
        raise RuntimeError("OpenAI client 未初始化。请配置 OPENAI_API_KEY。")
    prompt = f"""
Write a concise, compelling cover letter in {"Chinese (简体中文)" if lang=="zh" else "English"}.
Match the resume's background and the job needs.

[Job description or user instructions]
{jd_text or "(none)"}

[User focus directives]
{focus_directives or "(none)"}

[Resume]
{resume_text}

[Output requirement]
Return ONLY the cover letter body (no extra notes).
"""
    rsp = client.chat.completions.create(
        model=MODEL_NAME,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.6,
    )
    return (rsp.choices[0].message.content or "").strip()

def to_docx_bytes(text: str) -> bytes:
    """将纯文本导出为 .docx"""
    doc = Document()
    for line in (text or "").split("\n"):
        doc.add_paragraph(line)
    buf = io.BytesIO()
    doc.save(buf)
    buf.seek(0)
    return buf.getvalue()

# ============= SessionState（下载不丢） =============
for k, v in {
    "optimized_resume": None,
    "cover_letter": None,
    "detected_lang": None,
    "last_file_format": None,
}.items():
    if k not in st.session_state:
        st.session_state[k] = v

# ============= 左侧栏设置 =============
st.sidebar.header("设置")
st.sidebar.caption("以下选项仅影响生成策略")

FOCUS_OPTIONS = ["业务影响", "量化成果", "项目管理", "沟通协作", "领导力", "技术深度", "AI/数据分析", "研究能力", "客户价值"]
focus_tags = st.sidebar.multiselect("精修侧重（可多选）", FOCUS_OPTIONS, default=["业务影响", "量化成果"])

custom_points = st.sidebar.text_area(
    "增强点（可自定义）",
    placeholder="例如：突出X行业经验；量化每段成果；强调跨团队协作…",
    height=90
)

need_cl = st.sidebar.checkbox("生成求职信（Cover Letter）", value=True)

use_ocr = st.sidebar.checkbox(
    "启用 OCR（扫描 PDF）",
    value=False,
    help="若 PDF 是扫描件且提取不到文本，开启后尝试识别（云端若缺少依赖将自动降级并提示）。"
)

# ============= 主体区域 =============
st.title("🧠 AI 智能简历优化")
st.caption("上传简历，AI 将根据 JD/指令一键优化；可选生成求职信（Cover Letter，语言自动随简历）。")

col_left, col_right = st.columns([1, 1])
with col_left:
    # 只允许 PDF / DOCX，≤50MB
    MAX_UPLOAD_MB = 50
    uploaded_file = st.file_uploader(
        "上传简历（PDF 或 DOCX）",
        type=["pdf", "docx"],
        accept_multiple_files=False,
        help="单文件 ≤ 50MB；如果是扫描件，请在左侧开启 OCR。"
    )

    # 大小 & 后缀校验
    if uploaded_file is not None:
        try:
            size_bytes = getattr(uploaded_file, "size", None)
            if size_bytes is None:
                size_bytes = len(uploaded_file.getbuffer())
        except Exception:
            size_bytes = None

        if size_bytes is not None and size_bytes > MAX_UPLOAD_MB * 1024 * 1024:
            mb = size_bytes / (1024 * 1024)
            st.error(f"文件过大（{mb:.1f} MB）。请压缩至 {MAX_UPLOAD_MB}MB 以内后再上传。")
            st.stop()

        name = uploaded_file.name.lower()
        if not (name.endswith(".pdf") or name.endswith(".docx")):
            st.error("当前版本仅支持 PDF 或 DOCX 文件。")
            st.stop()

with col_right:
    jd_text = st.text_area(
        "粘贴目标职位 JD 或优化指令（可批量、用分隔）",
        placeholder="例如：Actuarial graduate role at Deloitte. 请重点突出数据分析与建模能力；Cover Letter 更正式。",
        height=150
    )

st.info("💡 提示：可在左侧设置“精修侧重/增强点”；若 PDF 为扫描件，可开启 OCR。", icon="💡")

# 使用 form 让按钮始终可见
with st.form("gen_form", clear_on_submit=False):
    submitted = st.form_submit_button("🚀 一键生成", use_container_width=True)

# ============= 生成处理 =============
if submitted:
    if not uploaded_file:
        st.warning("请先上传简历文件（PDF / DOCX）。")
    elif not OPENAI_API_KEY:
        st.error("未配置 OPENAI_API_KEY，无法调用模型。")
    else:
        try:
            with st.spinner("AI 正在分析并优化中，请稍候…"):
                resume_text, fmt = read_resume(uploaded_file, use_ocr=use_ocr)
                lang = detect_language(resume_text)
                st.session_state.detected_lang = lang
                st.session_state.last_file_format = fmt

                focus_directives = build_focus_instructions(focus_tags, custom_points, lang)

                optimized = llm_optimize_resume(resume_text, jd_text, lang, focus_directives)
                st.session_state.optimized_resume = optimized

                if need_cl:
                    cl = llm_cover_letter(resume_text, jd_text, lang, focus_directives)
                    st.session_state.cover_letter = cl
                else:
                    st.session_state.cover_letter = None

            lang_badge = "中文" if st.session_state.detected_lang == "zh" else "English"
            st.success(f"已完成！检测语言：**{lang_badge}**，来源：**{st.session_state.last_file_format}**。请在下方查看与下载。")

            # 如果 OCR 开启但依赖缺失，给友好提示
            if use_ocr and st.session_state.last_file_format == "PDF" and not _safe_import_ocr()[0]:
                st.warning("已尝试 OCR，但运行环境可能缺少依赖（Tesseract/Poppler）。请在本地或自建环境安装后再试。")

        except Exception as e:
            st.error(f"❌ 出错：{e}")

# ============= 结果展示 / 下载（不会因下载而清空） =============
if st.session_state.optimized_resume:
    st.subheader("✅ 优化后的简历")
    st.text_area("Resume Preview", st.session_state.optimized_resume, height=360)
    st.download_button(
        "📄 下载优化简历（Word）",
        data=to_docx_bytes(st.session_state.optimized_resume),
        file_name="Optimized_Resume.docx",
        mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        use_container_width=True
    )

if st.session_state.cover_letter:
    st.subheader("📬 求职信（Cover Letter）")
    st.text_area("Cover Letter Preview", st.session_state.cover_letter, height=280)
    st.download_button(
        "📄 下载求职信（Word）",
        data=to_docx_bytes(st.session_state.cover_letter),
        file_name="Cover_Letter.docx",
        mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        use_container_width=True
    )

st.markdown("---")
st.caption("© 2025 AI Resume Optimizer｜仅供个人求职使用，禁止商用与爬取。")