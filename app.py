import os
import io
import re
from typing import Tuple

import streamlit as st
from dotenv import load_dotenv
from docx import Document

# 条件导入（OCR 相关库在云端不一定可用）
try:
    import pdfplumber
except Exception:
    pdfplumber = None

# OCR 依赖：可能在云端不可用，运行时做检测
def _safe_import_ocr():
    try:
        import pytesseract  # type: ignore
        from pdf2image import convert_from_bytes  # type: ignore
        return pytesseract, convert_from_bytes
    except Exception:
        return None, None

from openai import OpenAI

# ========= 页面配置 & 样式修复 =========
st.set_page_config(page_title="AI 智能简历优化", page_icon="🧠", layout="wide")
st.markdown("""
<style>
/* 修复标题被遮挡：保留 Header 高度，给内容加上内边距 */
[data-testid="stHeader"]{visibility:visible;height:2.8rem;background:transparent;}
[data-testid="stToolbar"]{visibility:hidden;height:2.8rem;}
.block-container{padding-top:3.2rem!important;max-width:1200px;}
h1:first-child,.stMarkdown h1:first-child{margin-top:0.6rem!important;}
/* 让提示、按钮更醒目一些 */
button[kind="primary"] { font-weight: 600; }
</style>
""", unsafe_allow_html=True)

# ========= 载入 OpenAI =========
load_dotenv()
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
if not OPENAI_API_KEY:
    st.error("⚠️ 未检测到 OPENAI_API_KEY。请在 Streamlit → Settings → Secrets 添加：\nOPENAI_API_KEY = \"sk-xxxx\"")
client = OpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None

# ========= 工具函数 =========
def detect_language(text: str) -> str:
    """简单检测：中文多则 zh，否则 en"""
    zh = len(re.findall(r'[\u4e00-\u9fff]', text))
    en = len(re.findall(r'[A-Za-z]', text))
    return "zh" if zh > en else "en"

def _read_pdf_text(file_bytes: bytes) -> str:
    """优先使用 pdfplumber 提取文本"""
    if not pdfplumber:
        return ""
    text = ""
    with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
        for p in pdf.pages:
            text += (p.extract_text() or "") + "\n"
    return text.strip()

def _ocr_pdf(file_bytes: bytes) -> str:
    """用 OCR 从扫描 PDF 中识别文本（若运行环境缺库则返回空字符串）"""
    pytesseract, convert_from_bytes = _safe_import_ocr()
    if not (pytesseract and convert_from_bytes):
        return ""
    try:
        images = convert_from_bytes(file_bytes, dpi=300)
        parts = []
        for im in images:
            parts.append(pytesseract.image_to_string(im, lang="chi_sim+eng"))
        return "\n".join(parts).strip()
    except Exception:
        return ""

def read_resume(uploaded_file, use_ocr: bool) -> Tuple[str, str]:
    """
    读取 PDF/DOCX/TXT；返回 (文本, 格式名)
    - 若为 PDF 且文本极少，且开启 OCR，则尝试 OCR。
    """
    name = uploaded_file.name.lower()
    raw = uploaded_file.read()
    uploaded_file.seek(0)

    if name.endswith(".pdf"):
        text = _read_pdf_text(raw)
        # 判定是否扫描件（文本极少）
        if use_ocr and len(text) < 50:
            ocr_text = _ocr_pdf(raw)
            if ocr_text:
                return ocr_text, "PDF(OCR)"
            else:
                # OCR 不可用或失败
                return text or "", "PDF"
        return text, "PDF"

    elif name.endswith(".docx"):
        doc = Document(io.BytesIO(raw))
        return "\n".join(p.text for p in doc.paragraphs).strip(), "DOCX"

    elif name.endswith(".txt"):
        try:
            return raw.decode("utf-8").strip(), "TXT"
        except Exception:
            return raw.decode("latin-1", errors="ignore").strip(), "TXT"

    else:
        raise ValueError("仅支持 PDF / DOCX / TXT")

def build_focus_instructions(focus_tags, custom_points, lang):
    """根据侧栏的精修侧重、增强点，生成优化指令片段（中/英）"""
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
        model="gpt-4o-mini",
        messages=[{"role":"user","content":prompt}],
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
        model="gpt-4o-mini",
        messages=[{"role":"user","content":prompt}],
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

# ========= SessionState（下载不丢） =========
for k, v in {
    "optimized_resume": None,
    "cover_letter": None,
    "detected_lang": None,
    "last_file_format": None,
}.items():
    if k not in st.session_state:
        st.session_state[k] = v

# ========= 左侧栏：用户选项 =========
st.sidebar.header("设置")
st.sidebar.caption("以下选项仅影响生成策略")

# 精修侧重（多选）
FOCUS_OPTIONS = ["业务影响", "量化成果", "项目管理", "沟通协作", "领导力", "技术深度", "AI/数据分析", "研究能力", "客户价值"]
focus_tags = st.sidebar.multiselect("精修侧重（可多选）", FOCUS_OPTIONS, default=["业务影响", "量化成果"])

# 增强点（自定义）
custom_points = st.sidebar.text_area("增强点（可自定义）", placeholder="例如：突出X行业经验；量化每段成果；强调跨团队协作…", height=90)

# 生成求职信
need_cl = st.sidebar.checkbox("生成求职信（Cover Letter）", value=True)

# 启用 OCR（扫描PDF）
use_ocr = st.sidebar.checkbox("启用 OCR（扫描 PDF）", value=False,
                              help="若 PDF 是扫描件，开启后尝试 OCR 识别。云端若缺少 Tesseract/Poppler 将自动降级并提示。")

st.title("🧠 AI 智能简历优化")
st.caption("上传简历，AI 将根据 JD/指令一键优化；可选生成求职信（Cover Letter，语言自动随简历）。")

# 右侧主体
col_left, col_right = st.columns([1, 1])

with col_left:
    uploaded_file = st.file_uploader("上传简历（PDF / DOCX / TXT）", type=["pdf","docx","txt"], label_visibility="visible")

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

if submitted:
    if not uploaded_file:
        st.warning("请先上传简历文件（PDF / DOCX / TXT）。")
    elif not OPENAI_API_KEY:
        st.error("未配置 OPENAI_API_KEY，无法调用模型。")
    else:
        try:
            with st.spinner("AI 正在分析并优化中，请稍候…"):
                resume_text, fmt = read_resume(uploaded_file, use_ocr=use_ocr)
                if not resume_text:
                    st.error("未能从简历中解析出文本。若为扫描 PDF，请尝试勾选“启用 OCR”。")
                else:
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

            if st.session_state.optimized_resume:
                lang_badge = "中文" if st.session_state.detected_lang == "zh" else "English"
                st.success(f"已完成！检测语言：**{lang_badge}**，来源：**{st.session_state.last_file_format}**。请在下方查看与下载。")

                if use_ocr and st.session_state.last_file_format == "PDF" and pdfplumber and len(st.session_state.optimized_resume) < 50:
                    st.warning("看起来 PDF 可能是扫描件，且 OCR 未可用或未识别到文本。若在云端，请确认 Poppler / Tesseract 依赖。")

        except Exception as e:
            st.error(f"❌ 出错：{e}")

# ======= 结果展示/下载（保持在页面上，不会因下载而消失） =======
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