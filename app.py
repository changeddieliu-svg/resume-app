# app_user_refined.py — 正式版外观（单一模式；求职信语言自动；无 ATS 检测/无多版本/无支付）
# 依赖：streamlit pdfplumber python-docx python-dotenv reportlab pdf2image pytesseract pillow openai

import os, io, re, json
from typing import List, Dict, Tuple
import streamlit as st
import pdfplumber
from dotenv import load_dotenv
from docx import Document

# ---------- 可选 PDF 导出 ----------
try:
    from reportlab.pdfgen import canvas
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    HAS_PDF = True
except Exception:
    HAS_PDF = False

# ---------- 可选 OCR ----------
_HAS_OCR = True
try:
    from pdf2image import convert_from_bytes
    import pytesseract
    from PIL import Image  # noqa: F401
except Exception:
    _HAS_OCR = False

# ---------- OpenAI ----------
load_dotenv()
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
client = None
if OPENAI_API_KEY:
    try:
        from openai import OpenAI
        client = OpenAI(api_key=OPENAI_API_KEY)
    except Exception:
        client = None

# ======================================================
# 工具函数
# ======================================================
def to_plain_text(x) -> str:
    if isinstance(x, str): return x
    try:
        return json.dumps(x, ensure_ascii=False, indent=2)
    except Exception:
        return str(x)

def extract_text_from_pdf_bytes(data: bytes, enable_ocr=True) -> Tuple[str, bool, int, str]:
    text, used_ocr, pages_count, lang_hint = "", False, 0, ""
    try:
        with pdfplumber.open(io.BytesIO(data)) as pdf:
            pages = []
            for p in pdf.pages:
                pages_count += 1
                pages.append(p.extract_text() or "")
            text = "\n".join(pages).strip()
    except Exception:
        text = ""
    if enable_ocr and len(text) < 80:
        if not _HAS_OCR:
            return text, False, pages_count, ""
        try:
            images = convert_from_bytes(data, dpi=300)
            ocr_out = [pytesseract.image_to_string(img, lang="chi_sim+eng") for img in images]
            text = "\n".join(ocr_out).strip()
            used_ocr = True
            pages_count = len(images) if pages_count == 0 else pages_count
            lang_hint = "chi_sim+eng"
            return text, used_ocr, pages_count, lang_hint
        except Exception:
            pass
    return text, used_ocr, pages_count, lang_hint

def extract_text_from_docx(file) -> str:
    doc = Document(file)
    return "\n".join([p.text for p in doc.paragraphs]).strip()

def make_docx_from_text(text: str) -> bytes:
    doc = Document()
    for line in to_plain_text(text).splitlines():
        doc.add_paragraph(line)
    bio = io.BytesIO(); doc.save(bio); return bio.getvalue()

def make_pdf_from_text(text: str) -> bytes:
    if not HAS_PDF:
        raise RuntimeError("缺少 reportlab：pip install reportlab")
    bio = io.BytesIO(); c = canvas.Canvas(bio, pagesize=A4)
    width, height = A4; margin = 15*mm; y = height - margin
    c.setFont("Helvetica", 10)
    for line in to_plain_text(text).splitlines():
        if y < margin:
            c.showPage(); c.setFont("Helvetica", 10); y = height - margin
        c.drawString(margin, y, line[:110]); y -= 6*mm
    c.showPage(); c.save(); return bio.getvalue()

def robust_json_loads(s: str):
    try:
        return json.loads(s)
    except Exception:
        pass
    s2 = s.strip()
    if s2.startswith("```"):
        s2 = re.sub(r"^```[a-zA-Z]*", "", s2).strip()
        if s2.endswith("```"):
            s2 = s2[:-3]
    try:
        return json.loads(s2)
    except Exception:
        pass
    start, end = s.find("{"), s.rfind("}")
    if start != -1 and end != -1 and end > start:
        return json.loads(s[start:end+1])
    raise ValueError("无法从模型输出中解析有效 JSON。")

def infer_title_from_filename(name: str) -> str:
    if not name: return "Curriculum Vitae"
    base = re.sub(r"\.(pdf|docx)$", "", name, flags=re.I)
    base = re.sub(r"[_-]+", " ", base).strip()
    base = re.sub(r"(?i)optimized\s*resume", "", base).strip()
    if not base: return "Curriculum Vitae"
    return f"{base} – CV"

# ---------- 简历语言检测（决定求职信语言） ----------
def detect_resume_language(text: str) -> str:
    """返回 'en' or 'zh'（启发式）：中文汉字数 vs 英文字母数；默认英文"""
    chinese = sum(1 for c in text if '\u4e00' <= c <= '\u9fff')
    english = sum(1 for c in text if c.isascii() and c.isalpha())
    return "zh" if chinese > english else "en"

# ======================================================
# OpenAI 调用
# ======================================================
BASE_TASK = """
【原始简历】
{resume_text}

【目标职位JD】
{jd_text}

任务：
1) 抽取3-8条代表性的 before_bullets；
2) 产出与JD对齐的 after_bullets（动词开头、可量化）；
3) 生成 optimized_resume（按清晰分节，单列、无表格/图片，便于机器解析）；
4) {cover_directive}
5) 仅生成 1 个主版本。

返回严格 JSON：
{{
  "optimized_resume": "…",
  "match_score": 0,
  "missing_keywords": [],
  "suggested_bullets": [],
  "notes": "",
  "before_bullets": [],
  "after_bullets": [],
  "cover_letter": ""
}}
"""

def build_prompt(resume_text: str, jd_text: str, refine: List[str], emphasis: str,
                 want_cover: bool, cl_lang: str) -> str:
    refine_str = "、".join(refine) if refine else "均衡"
    # 求职信语言指令
    if want_cover:
        if cl_lang == "zh":
            cover_directive = "生成中文求职信（Cover Letter），≤ 1 页，正式职场语气。"
        else:
            cover_directive = "Generate an English cover letter (≤ 1 page, professional tone)."
    else:
        cover_directive = "无需生成求职信（Cover Letter）。"

    head = f"""你是一名资深职业顾问。
请根据以下简历与职位描述进行专业优化。
精修侧重：{refine_str}；强调点：{emphasis or '数据驱动、可量化、关键词契合'}。

语言要求：
- optimized_resume：沿用原简历语言（中文→中文，英文→英文）。
- cover_letter：严格按照上面的语言指令（与简历语言一致）。"""
    return head + BASE_TASK.format(
        resume_text=resume_text,
        jd_text=jd_text,
        cover_directive=cover_directive
    )

def call_openai_json(prompt: str) -> Dict:
    if client is None:
        return {
            "optimized_resume": "Demo mode: 请配置 OPENAI_API_KEY。",
            "match_score": 0, "missing_keywords": [], "suggested_bullets": [],
            "notes": "未配置 OPENAI_API_KEY。", "before_bullets": [], "after_bullets": [],
            "cover_letter": ""
        }
    # 正式外观版使用轻量模型，响应更快；如需更强可切换 gpt-4o
    resp = client.chat.completions.create(
        model="gpt-4o-mini",
        temperature=0.2,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": "You are an expert resume optimizer. Respond ONLY JSON."},
            {"role": "user", "content": prompt}
        ]
    )
    raw = resp.choices[0].message.content
    try:
        return robust_json_loads(raw)
    except Exception:
        return {"optimized_resume": raw, "match_score": 0, "missing_keywords": [],
                "suggested_bullets": [], "notes": "", "cover_letter": ""}

# ======================================================
# UI
# ======================================================
st.set_page_config(page_title="AI 智能简历优化", page_icon="🧩", layout="wide")
st.title("🧩 AI 智能简历优化")
st.caption("让你的简历更符合 HR 与算法的语言。可选自动生成求职信（Cover Letter）。")

# session state
if "results" not in st.session_state: st.session_state.results = []
if "params" not in st.session_state: st.session_state.params = {}
if "export_title" not in st.session_state: st.session_state.export_title = ""
if "resume_lang" not in st.session_state: st.session_state.resume_lang = "en"

# Sidebar（单一模式）
with st.sidebar:
    st.header("设置")
    st.caption("请选择优化参数：")
    tone = st.selectbox("语气", ["专业", "自信", "结果导向", "谦逊"], index=0)
    refine = st.multiselect("精修侧重", ["技术深度", "业务影响", "领导力", "沟通协作"], default=["业务影响"])
    emphasis = st.text_input("强调点", value="数据驱动、可量化、关键词契合")
    want_cover = st.checkbox("生成 求职信（Cover Letter，自动随简历语言）", value=True)
    st.divider()
    enable_ocr = st.checkbox("启用 OCR（扫描PDF）", value=True)

left, right = st.columns([1, 1])
with left:
    uploaded = st.file_uploader("上传简历（PDF 或 DOCX）", type=["pdf", "docx"])
with right:
    jd_text = st.text_area("粘贴目标职位 JD（可批量，--- 分隔）", height=200)

run = st.button("🚀 一键生成", type="primary", use_container_width=True)

def split_jd_blocks(text: str):
    if not text.strip(): return []
    return [b.strip() for b in text.split("\n---\n") if b.strip()]

def split_and_run(resume_text: str, jd_text: str, cl_lang: str):
    blocks = split_jd_blocks(jd_text) or [jd_text.strip()]
    results = []
    for idx, jd in enumerate(blocks, start=1):
        prompt = build_prompt(resume_text, jd, refine=refine, emphasis=emphasis,
                              want_cover=want_cover, cl_lang=cl_lang)
        data = call_openai_json(prompt)
        data["_jd_idx"] = idx
        data["_jd_excerpt"] = (jd[:120] + "…") if len(jd) > 120 else jd
        results.append(data)
    return results

if run:
    if not uploaded or not jd_text.strip():
        st.error("请上传简历并粘贴 JD。")
    else:
        # 文件名→导出标题
        st.session_state.export_title = re.sub(r"[\\/]", "-", re.sub(r"\.(pdf|docx)$","",uploaded.name, flags=re.I)).strip() or "Curriculum Vitae"
        # 解析文件
        if uploaded.name.lower().endswith(".pdf"):
            data = uploaded.getvalue()
            resume_text, used_ocr, pages, ocr_lang_used = extract_text_from_pdf_bytes(data, enable_ocr=enable_ocr)
        else:
            resume_text = extract_text_from_docx(uploaded); used_ocr, pages, ocr_lang_used = False, None, None

        if not resume_text.strip():
            st.error("未从文件中提取到文本。若为扫描PDF，请启用 OCR 并安装依赖。")
        else:
            # 检测简历语言（决定求职信语言）
            cl_lang = detect_resume_language(resume_text)
            st.session_state.resume_lang = cl_lang
            st.session_state.results = split_and_run(resume_text, jd_text, cl_lang=cl_lang)
            st.session_state.params = {
                "tone": tone, "refine": refine, "emphasis": emphasis,
                "want_cover": want_cover, "ocr_used": used_ocr, "ocr_lang": ocr_lang_used,
                "resume_lang": cl_lang
            }

def current_result():
    if not st.session_state.results: return None, None
    lst = st.session_state.results
    if len(lst) == 1: return lst[0], f"JD#{lst[0]['_jd_idx']}"
    labels = [f"JD#{r['_jd_idx']} · {r['_jd_excerpt']}" for r in lst]
    sel = st.selectbox("选择查看的 JD 结果：", labels, index=0)
    return lst[labels.index(sel)], sel

# ---------- 展示 ----------
tabs = st.tabs(["⭐ 优化后简历", "✉️ 求职信（Cover Letter）", "📤 导出"])

with tabs[0]:
    res, label = current_result()
    if not res:
        st.info("先上传并生成。")
    else:
        lang_badge = "中文" if st.session_state.get('resume_lang') == 'zh' else "English"
        ocr_status = "ON" if st.session_state.params.get('ocr_used') else "OFF"
        ocr_lang = st.session_state.params.get('ocr_lang')
        ocr_extra = f" ({ocr_lang})" if ocr_lang else ""
        st.markdown(f"**{label}** · 简历语言：{lang_badge} · OCR: {ocr_status}{ocr_extra}")
        st.code(to_plain_text(res.get("optimized_resume","")), language="markdown")

with tabs[1]:
    res, _ = current_result()
    if not res:
        st.info("暂无结果。")
    else:
        cl = (res.get("cover_letter","") or "").strip()
        if cl:
            st.code(to_plain_text(cl), language="markdown")
        else:
            st.info("未生成求职信（Cover Letter）。可在左侧勾选后重新生成。")

with tabs[2]:
    res, _ = current_result()
    if not res:
        st.info("暂无结果。")
    else:
        export_title = (st.session_state.get('export_title') or 'Curriculum Vitae').strip()
        txt = (res.get("optimized_resume","") or "").strip()

        # 仅导出（无 ATS 检测）
        try:
            st.download_button("⬇️ 下载 DOCX（主版本）", data=make_docx_from_text(txt),
                               file_name=f"{export_title}.docx",
                               mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                               use_container_width=True)
        except Exception as e:
            st.warning(f"DOCX 导出失败：{e}")

        if HAS_PDF:
            try:
                st.download_button("⬇️ 下载 PDF（主版本）", data=make_pdf_from_text(txt),
                                   file_name=f"{export_title}.pdf",
                                   mime="application/pdf", use_container_width=True)
            except Exception as e:
                st.warning(f"PDF 导出失败：{e}")
        else:
            st.info("需要安装 reportlab 才能导出 PDF：pip install reportlab")

        cl = (res.get("cover_letter","") or "").strip()
        if cl:
            st.subheader("求职信（Cover Letter）导出")
            try:
                st.download_button("⬇️ 下载 DOCX（求职信）", data=make_docx_from_text(cl),
                                   file_name=f"{export_title}_求职信(CoverLetter).docx",
                                   mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                                   use_container_width=True)
            except Exception as e:
                st.warning(f"COVER DOCX 导出失败：{e}")
            if HAS_PDF:
                try:
                    st.download_button("⬇️ 下载 PDF（求职信）", data=make_pdf_from_text(cl),
                                       file_name=f"{export_title}_求职信(CoverLetter).pdf",
                                       mime="application/pdf", use_container_width=True)
                except Exception as e:
                    st.warning(f"COVER PDF 导出失败：{e}")