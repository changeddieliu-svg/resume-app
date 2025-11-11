import os
import io
import re
from typing import Tuple
import streamlit as st
import pdfplumber
from docx import Document
from dotenv import load_dotenv
from openai import OpenAI

# ========= 页面配置 & 样式修复 =========
st.set_page_config(page_title="AI 智能简历优化", page_icon="🧠", layout="centered")
st.markdown("""
<style>
/* 修复标题被遮挡：保留 Header 高度，给内容加上内边距 */
[data-testid="stHeader"]{visibility:visible;height:2.8rem;background:transparent;}
[data-testid="stToolbar"]{visibility:hidden;height:2.8rem;}
.block-container{padding-top:3.2rem!important;max-width:1100px;}
h1:first-child,.stMarkdown h1:first-child{margin-top:0.6rem!important;}
/* 让提示、按钮更醒目一些 */
button[kind="primary"] { font-weight: 600; }
</style>
""", unsafe_allow_html=True)

# ========= 载入 OpenAI =========
load_dotenv()
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
if not OPENAI_API_KEY:
    st.error("⚠️ 未检测到 OPENAI_API_KEY。请到 Streamlit → Settings → Secrets 添加：\nOPENAI_API_KEY = \"sk-xxxx\"")
client = OpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None

# ========= 工具函数 =========
def detect_language(text: str) -> str:
    """简单检测：中文多则 zh，否则 en"""
    zh = len(re.findall(r'[\u4e00-\u9fff]', text))
    en = len(re.findall(r'[A-Za-z]', text))
    return "zh" if zh > en else "en"

def read_resume(file) -> str:
    """读取 PDF/DOCX/TXT"""
    name = file.name.lower()
    if name.endswith(".pdf"):
        with pdfplumber.open(file) as pdf:
            return "\n".join((p.extract_text() or "") for p in pdf.pages).strip()
    elif name.endswith(".docx"):
        doc = Document(file)
        return "\n".join(p.text for p in doc.paragraphs).strip()
    elif name.endswith(".txt"):
        return file.read().decode("utf-8").strip()
    else:
        raise ValueError("仅支持 PDF / DOCX / TXT")

def llm_optimize_resume(resume_text: str, jd_text: str, lang: str) -> str:
    """调用模型生成优化简历"""
    if not client:
        raise RuntimeError("OpenAI client 未初始化。请配置 OPENAI_API_KEY。")
    prompt = f"""
You are a professional career consultant AI.
Optimize the following resume to better match the job description / user instructions.
Keep the same language as the resume: {"Chinese (简体中文)" if lang=="zh" else "English"}.
Focus on quantifiable achievements, clear structure, strong action verbs, ATS-friendly formatting.

[Job description or user instructions]
{jd_text or "(none)"}

[Original resume]
{resume_text}

[Output requirement]
Return ONLY the optimized resume text (no extra commentary).
"""
    rsp = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role":"user","content":prompt}],
        temperature=0.6,
    )
    return (rsp.choices[0].message.content or "").strip()

def llm_cover_letter(resume_text: str, jd_text: str, lang: str) -> str:
    """调用模型生成求职信"""
    if not client:
        raise RuntimeError("OpenAI client 未初始化。请配置 OPENAI_API_KEY。")
    prompt = f"""
Write a concise, compelling cover letter in {"Chinese (简体中文)" if lang=="zh" else "English"}.
Match the resume's background and the job needs.

[Job description or user instructions]
{jd_text or "(none)"}

[Resume]
{resume_text}

[Output requirement]
Return ONLY the cover letter body (no salutations beyond standard, no extra notes).
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

# ========= SessionState：记住结果（下载不丢失） =========
if "optimized_resume" not in st.session_state:
    st.session_state.optimized_resume = None
if "cover_letter" not in st.session_state:
    st.session_state.cover_letter = None
if "detected_lang" not in st.session_state:
    st.session_state.detected_lang = None

# ========= UI =========
st.title("🧠 AI 智能简历优化")
st.caption("上传简历，AI 将根据 JD 一键优化；可选生成求职信（Cover Letter，语言自动随简历）。")

with st.form("gen_form", clear_on_submit=False):
    uploaded_file = st.file_uploader("上传简历（PDF / DOCX / TXT）", type=["pdf","docx","txt"], label_visibility="visible")

    jd_text = st.text_area(
        "粘贴目标职位 JD 或优化指令（可批量、用分隔）",
        placeholder="例如：Actuarial graduate role at Deloitte. 请重点突出数据分析与建模能力；Cover Letter 更正式。",
        height=120
    )

    col1, col2 = st.columns([1, 3])
    with col1:
        need_cl = st.checkbox("生成求职信（Cover Letter）", value=True)
    with col2:
        st.info("💡 可在右侧输入框写“请突出某技能、指定行业、写法”等优化要求。")

    submitted = st.form_submit_button("🚀 一键生成", use_container_width=True)

# ======= 点击提交后处理 =======
if submitted:
    if not uploaded_file:
        st.warning("请先上传简历文件（PDF / DOCX / TXT）。")
    elif not OPENAI_API_KEY:
        st.error("未配置 OPENAI_API_KEY，无法调用模型。")
    else:
        try:
            with st.spinner("AI 正在分析并优化中，请稍候…"):
                resume_text = read_resume(uploaded_file)
                lang = detect_language(resume_text)
                optimized = llm_optimize_resume(resume_text, jd_text, lang)
                st.session_state.optimized_resume = optimized
                st.session_state.detected_lang = lang

                if need_cl:
                    cl = llm_cover_letter(resume_text, jd_text, lang)
                    st.session_state.cover_letter = cl
                else:
                    st.session_state.cover_letter = None

            st.success("已完成！请在下方查看与下载。")

        except Exception as e:
            st.error(f"❌ 出错：{e}")

# ======= 结果展示/下载（保持在页面上，不会因下载而消失） =======
if st.session_state.optimized_resume:
    st.subheader("✅ 优化后的简历")
    st.text_area("Resume Preview", st.session_state.optimized_resume, height=320)
    st.download_button(
        "📄 下载优化简历（Word）",
        data=to_docx_bytes(st.session_state.optimized_resume),
        file_name="Optimized_Resume.docx",
        mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        use_container_width=True
    )

if st.session_state.cover_letter:
    st.subheader("📬 求职信（Cover Letter）")
    st.text_area("Cover Letter Preview", st.session_state.cover_letter, height=260)
    st.download_button(
        "📄 下载求职信（Word）",
        data=to_docx_bytes(st.session_state.cover_letter),
        file_name="Cover_Letter.docx",
        mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        use_container_width=True
    )

st.markdown("---")
st.caption("© 2025 AI Resume Optimizer｜仅供个人求职使用，禁止商用与爬取。")