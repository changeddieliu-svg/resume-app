# app.py
# 🧠 AI 智能简历优化（支持中英自动识别、JD或优化指令输入、Cover Letter、下载后不丢失结果）

import os, io, re
from typing import Tuple, Optional
import streamlit as st

# =============== 环境变量与依赖检测 ===============
try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

import pdfplumber
from docx import Document

_HAS_PDF = True
try:
    from reportlab.pdfgen import canvas
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
except Exception:
    _HAS_PDF = False

_HAS_OCR = True
try:
    from pdf2image import convert_from_bytes
    import pytesseract
    from PIL import Image
except Exception:
    _HAS_OCR = False

from openai import OpenAI

# =============== 页面配置 ===============
st.set_page_config(page_title="AI 智能简历优化", page_icon="🧠", layout="centered")
st.markdown("""
<style>
[data-testid="stToolbar"], #MainMenu, footer {visibility:hidden;height:0;}
.block-container {padding-top:1rem;}
</style>
""", unsafe_allow_html=True)

# =============== OpenAI 初始化 ===============
def get_openai_client() -> OpenAI:
    api_key = st.secrets.get("OPENAI_API_KEY", os.getenv("OPENAI_API_KEY", ""))
    if not api_key:
        st.error("未检测到 OPENAI_API_KEY，请在 Streamlit Secrets 或 .env 中配置。")
        st.stop()
    return OpenAI(api_key=api_key)

def get_model_name() -> str:
    return st.secrets.get("MODEL_NAME", os.getenv("MODEL_NAME", "gpt-4o-mini"))

# =============== 简历语言检测 ===============
try:
    from langdetect import detect as _langdetect
    _HAS_LANGDETECT = True
except Exception:
    _HAS_LANGDETECT = False

def detect_lang_en_zh(text: str) -> str:
    if _HAS_LANGDETECT:
        try:
            code = _langdetect(text)
            if code.startswith("zh"): return "zh"
            if code.startswith("en"): return "en"
        except Exception: pass
    if re.search(r'[\u4e00-\u9fff]', text): return "zh"
    return "en"

# =============== Prompt 模板 ===============
EN_RESUME_PROMPT = """You are an expert resume editor. KEEP THE OUTPUT IN ENGLISH.
Rewrite the resume content to be concise, quantified and aligned to the target JD.
Use strong action verbs and measurable outcomes. Do NOT invent experience.
Return ONLY the optimized resume text."""
ZH_RESUME_PROMPT = """你是资深简历优化顾问，请用中文优化简历：
保持专业、精炼、可量化，突出与目标JD的匹配度。不要虚构经历。
只返回优化后的简历正文。"""
EN_CL_PROMPT = """Write a concise one-page English cover letter tailored to the resume and JD."""
ZH_CL_PROMPT = """请用中文撰写一页内的求职信，结合简历与目标JD。"""

def get_prompts(lang: str):
    if lang == "zh":
        return ZH_RESUME_PROMPT, ZH_CL_PROMPT, "务必使用中文输出。"
    return EN_RESUME_PROMPT, EN_CL_PROMPT, "Always respond in English."

# =============== 文件读取 ===============
def read_docx(b): return "\n".join([p.text for p in Document(io.BytesIO(b)).paragraphs if p.text])
def read_pdf(b):
    text=[]; 
    with pdfplumber.open(io.BytesIO(b)) as pdf:
        for p in pdf.pages: text.append(p.extract_text() or "")
    return "\n".join(text)
def parse_resume(f, use_ocr: bool) -> Tuple[str,str]:
    b=f.read(); name=f.name.lower()
    if name.endswith(".pdf"):
        t=read_pdf(b)
        if use_ocr and len(t)<50 and _HAS_OCR:
            t="\n".join([pytesseract.image_to_string(i) for i in convert_from_bytes(b)])
        return t,"pdf"
    if name.endswith(".docx"): return read_docx(b),"docx"
    return b.decode("utf-8","ignore"),"txt"

# =============== AI 调用 ===============
def call_openai(msgs): 
    return get_openai_client().chat.completions.create(model=get_model_name(),messages=msgs).choices[0].message.content.strip()

# =============== 导出 DOCX & PDF ===============
from docx.shared import Pt
from docx.enum.text import WD_PARAGRAPH_ALIGNMENT
from docx.oxml.ns import qn
from docx.oxml import OxmlElement

def export_docx(text:str, lang:str="en")->bytes:
    d=Document(); d.styles['Normal'].font.name='Calibri'; d.styles['Normal'].font.size=Pt(11)
    s=d.styles['Normal']._element.get_or_add_rPr(); r=s.find(qn('w:rFonts')) or OxmlElement('w:rFonts'); s.append(r)
    r.set(qn('w:eastAsia'),'Microsoft YaHei' if lang=='zh' else 'Calibri')
    for line in text.splitlines():
        p=d.add_paragraph(line.strip() or ""); p.alignment=WD_PARAGRAPH_ALIGNMENT.LEFT
    out=io.BytesIO(); d.save(out); out.seek(0); return out.getvalue()

def export_pdf(text:str)->bytes:
    if not _HAS_PDF: return b""
    b=io.BytesIO(); c=canvas.Canvas(b,pagesize=A4); w,h=A4; y=h-20*mm; c.setFont("Helvetica",10)
    for line in text.splitlines():
        if y<20*mm: c.showPage(); y=h-20*mm; c.setFont("Helvetica",10)
        c.drawString(20*mm,y,line[:110]); y-=6*mm
    c.save(); b.seek(0); return b.getvalue()

# =============== Session 状态 ===============
for k in ["opt_resume","opt_cl","resume_lang","export_title"]:
    if k not in st.session_state: st.session_state[k]=""

# =============== 界面布局 ===============
st.markdown("## 🧠 AI 智能简历优化")
st.caption("上传简历，AI 将根据 JD 一键优化；可选生成求职信（Cover Letter，语言自动随简历）。")

col1,col2=st.columns([1,1])
with col1:
    uploaded=st.file_uploader("上传简历（PDF 或 DOCX）",type=["pdf","docx","txt"])
with col2:
    jd_text=st.text_area(
        "粘贴目标职位 JD 或优化指令（可批量，用分隔）",
        height=180,
        placeholder="例如：Actuarial graduate role at Deloitte. 请重点突出数据分析与建模能力；Cover Letter 要自信正式。",
    )

# =============== 侧边栏设置 ===============
with st.sidebar:
    st.markdown("### 设置")
    pills=st.multiselect("精修侧重",["业务影响","沟通协作","领导力","技术深度","数据驱动"],default=["业务影响"])
    enhance=st.text_input("增强点（可自定义）","数据驱动、可量化、关键词契合")
    gen_cl=st.checkbox("生成求职信（Cover Letter）",True)
    use_ocr=st.checkbox("启用 OCR（扫描 PDF）",False)
    st.caption("⚠️ 仅供个人求职使用，禁止未授权复制或商用。")

# =============== 文件解析与语言检测 ===============
if uploaded:
    text,ftype=parse_resume(uploaded,use_ocr)
    st.session_state.export_title=re.sub(r'\.(pdf|docx|txt)$',"",uploaded.name,flags=re.I)
else: text,ftype="",""

if text:
    with st.expander("📄 简历文本预览",expanded=False):
        st.text_area("内容预览（前3000字）",text[:3000],height=200)
    lang=detect_lang_en_zh(text); st.session_state.resume_lang=lang
    st.markdown(f"🌐 检测语言：**{'中文' if lang=='zh' else 'English'}**")
else: lang="en"

# =============== 一键生成 ===============
if st.button("🪄 一键生成",type="primary",use_container_width=True,disabled=not uploaded):
    rp,cp,sys=get_prompts(lang)
    prefs=", ".join(pills)
    pref=f"{prefs}；{enhance}" if lang=="zh" else f"emphasize {prefs}, {enhance}"
    msgs=[
        {"role":"system","content":sys},
        {"role":"user","content":f"{rp}\n\n{'偏好' if lang=='zh' else 'Preference'}：{pref}"},
        {"role":"user","content":f"Resume:\n{text}\n\nTarget JD:\n{jd_text}"}
    ]
    with st.spinner("正在优化简历..."):
        res=call_openai(msgs)
    st.session_state.opt_resume=res
    if gen_cl:
        msgs=[{"role":"system","content":sys},{"role":"user","content":f"{cp}"},{"role":"user","content":f"Resume:\n{res}\n\nTarget JD:\n{jd_text}"}]
        with st.spinner("正在生成求职信..."):
            st.session_state.opt_cl=call_openai(msgs)

# =============== 导出区 ===============
opt_resume, opt_cl = st.session_state.opt_resume, st.session_state.opt_cl
if opt_resume:
    tabs=["⭐ 优化后简历"]; 
    if gen_cl and opt_cl: tabs.append("📄 求职信（Cover Letter）")
    tabs.append("📤 导出")
    t1,*rest=st.tabs(tabs)
    with t1: st.markdown(opt_resume.replace("\n","  \n"))
    if gen_cl and opt_cl:
        with rest[0]: st.markdown(opt_cl.replace("\n","  \n"))
    with rest[-1]:
        st.download_button("⬇️ 下载简历（DOCX）",export_docx(opt_resume,lang),f"{st.session_state.export_title}.docx","application/vnd.openxmlformats-officedocument.wordprocessingml.document",use_container_width=True)
        pdf=export_pdf(opt_resume)
        if pdf: st.download_button("⬇️ 下载简历（PDF）",pdf,f"{st.session_state.export_title}.pdf","application/pdf",use_container_width=True)
        if gen_cl and opt_cl:
            st.download_button("⬇️ 下载求职信（DOCX）",export_docx(opt_cl,lang),f"{st.session_state.export_title}_CoverLetter.docx","application/vnd.openxmlformats-officedocument.wordprocessingml.document",use_container_width=True)
            cl_pdf=export_pdf(opt_cl)
            if cl_pdf: st.download_button("⬇️ 下载求职信（PDF）",cl_pdf,f"{st.session_state.export_title}_CoverLetter.pdf","application/pdf",use_container_width=True)

st.caption("💡 提示：可在右侧输入框写“请突出某技能、指定行业、写法”等优化要求。")