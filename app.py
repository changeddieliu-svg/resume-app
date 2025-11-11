import os
import io
import re
import json
import tempfile
from typing import List, Dict, Tuple
import streamlit as st
import pdfplumber
from docx import Document
from dotenv import load_dotenv
from openai import OpenAI

# ---------- 初始化 ----------
st.set_page_config(page_title="AI 智能简历优化", page_icon="🧠", layout="wide")

# ---------- 样式修复（标题不再被遮挡） ----------
st.markdown("""
<style>
[data-testid="stHeader"] { 
  visibility: visible; 
  height: 2.8rem;
  background: transparent;
}
[data-testid="stToolbar"] { 
  visibility: hidden; 
  height: 2.8rem;
}
.block-container { 
  padding-top: 3.2rem !important; 
  max-width: 1100px;
}
h1:first-child, .stMarkdown h1:first-child { 
  margin-top: 0.6rem !important; 
}
</style>
""", unsafe_allow_html=True)

# ---------- 载入 OpenAI API ----------
load_dotenv()
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
if not OPENAI_API_KEY:
    st.error("⚠️ 请在 Streamlit Secrets 或 .env 文件中配置 OPENAI_API_KEY。")
client = OpenAI(api_key=OPENAI_API_KEY)

# ---------- 功能函数 ----------
def detect_language(text: str) -> str:
    """检测语言（简体中文 or 英文）"""
    chinese_count = len(re.findall(r'[\u4e00-\u9fff]', text))
    english_count = len(re.findall(r'[A-Za-z]', text))
    return "zh" if chinese_count > english_count else "en"

def read_file(file) -> str:
    """读取简历文本"""
    if file.name.endswith(".pdf"):
        with pdfplumber.open(file) as pdf:
            return "\n".join(page.extract_text() or "" for page in pdf.pages)
    elif file.name.endswith(".docx"):
        doc = Document(file)
        return "\n".join(p.text for p in doc.paragraphs)
    elif file.name.endswith(".txt"):
        return file.read().decode("utf-8")
    else:
        raise ValueError("不支持的文件格式")

def generate_resume_optimization(resume_text: str, jd_text: str, language: str) -> str:
    """生成优化后的简历文本"""
    prompt = f"""
You are a professional career consultant AI.
Optimize the following resume based on the provided job description.
Maintain the same language as the resume ({'Chinese' if language=='zh' else 'English'}).

Job description or custom instruction:
{jd_text}

Resume content:
{resume_text}

Provide a clean and well-formatted version.
    """
    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.7,
    )
    return response.choices[0].message.content.strip()

def generate_cover_letter(resume_text: str, jd_text: str, language: str) -> str:
    """生成求职信"""
    prompt = f"""
Write a concise and compelling cover letter in {'Chinese' if language=='zh' else 'English'}.
Ensure it matches the tone and content of the resume.

Job description / user request:
{jd_text}

Resume:
{resume_text}
    """
    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.7,
    )
    return response.choices[0].message.content.strip()

def export_docx(text: str, filename: str) -> bytes:
    """导出为 Word 文件"""
    doc = Document()
    for line in text.split("\n"):
        doc.add_paragraph(line)
    buffer = io.BytesIO()
    doc.save(buffer)
    buffer.seek(0)
    return buffer.getvalue()

# ---------- UI ----------
st.title("🧠 AI 智能简历优化")
st.caption("上传简历，AI 将根据 JD 一键优化；可选生成求职信（Cover Letter，语言自动随简历）。")

uploaded_file = st.file_uploader("上传简历（PDF 或 DOCX）", type=["pdf", "docx", "txt"])
jd_text = st.text_area("粘贴目标职位 JD 或优化指令（可批量、用分隔）",
                       placeholder="例如：Actuarial graduate role at Deloitte. 请重点突出数据分析与建模能力；Cover Letter 要更正式。")

col1, col2 = st.columns([1, 3])
with col1:
    generate_cl = st.checkbox("生成求职信（Cover Letter）", value=True)
with col2:
    st.info("💡 提示：可在右侧输入框写“请突出某技能、指定行业、写法”等优化要求。")

if uploaded_file and st.button("🚀 一键生成"):
    with st.spinner("AI 正在分析简历与 JD，请稍候..."):
        try:
            resume_text = read_file(uploaded_file)
            lang = detect_language(resume_text)
            optimized_resume = generate_resume_optimization(resume_text, jd_text, lang)
            cover_letter = generate_cover_letter(resume_text, jd_text, lang) if generate_cl else None

            # 展示结果
            st.subheader("✅ 优化后的简历")
            st.text_area("Resume Preview", optimized_resume, height=300)
            resume_docx = export_docx(optimized_resume, "Optimized_Resume.docx")
            st.download_button("📄 下载优化简历（Word）", resume_docx, file_name="Optimized_Resume.docx")

            if generate_cl and cover_letter:
                st.subheader("📬 求职信（Cover Letter）")
                st.text_area("Cover Letter Preview", cover_letter, height=250)
                cl_docx = export_docx(cover_letter, "Cover_Letter.docx")
                st.download_button("📄 下载求职信（Word）", cl_docx, file_name="Cover_Letter.docx")

        except Exception as e:
            st.error(f"❌ 出错啦：{e}")

st.markdown("---")
st.caption("© 2025 AI Resume Optimizer | 仅供个人求职使用，禁止商用与爬取。")