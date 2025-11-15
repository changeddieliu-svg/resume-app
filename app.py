import io
import os
import re
from typing import Tuple

import streamlit as st
from openai import OpenAI
import pdfplumber
from docx import Document

# OCR 相关（可选）
try:
    from pdf2image import convert_from_bytes
    import pytesseract

    HAS_OCR = True
except Exception:
    HAS_OCR = False

# ============== Analytics 安全导入 ==============
try:
    from analytics import log_event, log_feedback, log_error
except Exception:
    def log_event(*args, **kwargs):
        pass

    def log_feedback(*args, **kwargs):
        pass

    def log_error(*args, **kwargs):
        pass


# ============== OpenAI 客户端 ==============
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY", ""))


# ============== 工具函数 ==============

def detect_language(text: str) -> str:
    """非常轻量级的语言检测：统计中文字符占比，粗略判断中/英文。"""
    if not text:
        return "auto"

    chinese_chars = re.findall(r"[\u4e00-\u9fff]", text)
    ratio = len(chinese_chars) / max(len(text), 1)

    return "zh" if ratio > 0.15 else "en"


def read_docx(file_bytes: bytes) -> str:
    doc = Document(io.BytesIO(file_bytes))
    paragraphs = [p.text for p in doc.paragraphs]
    return "\n".join(p for p in paragraphs if p.strip())


def read_pdf(file_bytes: bytes, use_ocr: bool = False) -> str:
    text = ""

    # 先尝试用 pdfplumber 直接抽取文本
    try:
        with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
            for page in pdf.pages:
                page_text = page.extract_text() or ""
                text += page_text + "\n"
    except Exception:
        text = ""

    # 如果没抽到内容、并且用户勾选了 OCR，并且依赖可用，再走 OCR
    if use_ocr and HAS_OCR:
        try:
            images = convert_from_bytes(file_bytes)
            ocr_texts = []
            for img in images:
                ocr_texts.append(pytesseract.image_to_string(img))
            text = "\n".join(ocr_texts)
        except Exception as e:
            log_error("ocr_failed", e)

    return text.strip()


def build_prompt(
    base_cv: str,
    jd_or_instructions: str,
    refine_focus: list,
    custom_points: str,
    need_cover_letter: bool,
    lang: str,
) -> Tuple[str, str]:
    """
    返回： (cv_prompt, cover_letter_prompt)
    cover_letter_prompt 可能为空字符串（当不需要生成求职信时）
    """
    if lang == "zh":
        lang_tag = "Chinese"
        cv_title = "优化后的简历"
        cv_require = (
            "请在不虚构经历的前提下，优化结构、量化成果、突出与目标岗位匹配的经历，语言保持自然专业。"
        )
        cl_title = "求职信"
        cl_require = (
            "语言自然真诚、专业，控制在 3–6 段落，适合直接投递使用。"
        )
    else:
        lang_tag = "English"
        cv_title = "Optimized CV"
        cv_require = (
            "Do not fabricate experience. Improve structure, quantify impact, "
            "and highlight alignment with the target role in natural, professional English."
        )
        cl_title = "Cover Letter"
        cl_require = (
            "Use a natural, professional tone in English, 3–6 paragraphs, ready to send."
        )

    refine_str = ", ".join(refine_focus) if refine_focus else ""
    custom_str = custom_points.strip()

    extra_instruction_parts = []
    if refine_str:
        extra_instruction_parts.append(f"Refinement focus: {refine_str}")
    if custom_str:
        extra_instruction_parts.append(f"Custom requirements: {custom_str}")
    if jd_or_instructions.strip():
        extra_instruction_parts.append(
            f"Target JD / optimization instructions:\n{jd_or_instructions.strip()}"
        )

    extra_block = "\n\n".join(extra_instruction_parts) if extra_instruction_parts else ""

    cv_prompt = f"""
You are an expert {lang_tag} CV writer and career coach.

User's current CV:
-------------------
{base_cv}
-------------------

{extra_block}

Task:
Generate a rewritten version of the CV in {lang_tag}.
- Keep all experience factually true.
- Reorganize content for clarity.
- Quantify achievements where possible.
- Strongly highlight relevance to the target role.
- Return ONLY the final CV content, with clear sections (e.g. Education / Experience / Skills),
  without markdown bold syntax (** **) or bullet symbols that will break formatting in DOCX.
- Keep line breaks clean so that it can be safely placed into a Word document.

Output language: {lang_tag}
Title for the user (do NOT include in your output): {cv_title}

{cv_require}
""".strip()

    cover_letter_prompt = ""
    if need_cover_letter:
        cover_letter_prompt = f"""
You are an expert {lang_tag} cover letter writer.

User's current CV:
-------------------
{base_cv}
-------------------

{extra_block}

Task:
Write a tailored cover letter in {lang_tag} for this candidate, suitable for the target role.
- Tie the candidate's experience to the JD.
- Adopt a natural, confident but not exaggerated tone.
- 3–6 paragraphs.
- Do NOT wrap text with markdown symbols (** or *), output plain text only.
- Start with an appropriate greeting and end with a professional closing.

Output language: {lang_tag}
Title for the user (do NOT include in your output): {cl_title}

{cl_require}
""".strip()

    return cv_prompt, cover_letter_prompt


def call_openai(prompt: str, lang: str) -> str:
    """调用 OpenAI 生成文本。"""
    if not client.api_key:
        raise RuntimeError("OPENAI_API_KEY is not set.")

    system_msg = (
        "You are a helpful assistant for CV and cover letter rewriting." if lang == "en"
        else "你是一名专业的人力资源与职业教练专家，专门帮助候选人优化简历和求职信。"
    )

    resp = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": system_msg},
            {"role": "user", "content": prompt},
        ],
        temperature=0.5,
    )
    return resp.choices[0].message.content.strip()


def export_docx(text: str, title: str) -> bytes:
    """将纯文本写入 DOCX 并返回字节。避免复杂格式导致奇怪符号。"""
    doc = Document()
    for line in text.split("\n"):
        # 去掉多余空行
        if line.strip():
            doc.add_paragraph(line.strip())
        else:
            doc.add_paragraph("")
    buf = io.BytesIO()
    doc.save(buf)
    buf.seek(0)
    return buf.read()


# ============== 页面 & UI ==============

st.set_page_config(
    page_title="AI 智能简历优化",
    page_icon="🧠",
    layout="wide",
)

# 隐藏 streamlit 默认菜单、footer，并减小顶部空白
st.markdown(
    """
    <style>
    /* 隐藏右上角菜单与左上角汉堡 / 页脚 */
    #MainMenu {visibility: hidden;}
    footer {visibility: hidden;}
    header {visibility: hidden;}

    /* 整体页面稍微上移，减少顶部留白 */
    .block-container {
        padding-top: 1rem;
        padding-bottom: 2rem;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

# 日志：页面浏览
log_event("page_view", {"page": "resume_optimizer"})

# 左右布局
sidebar = st.sidebar
main_col, right_col = st.columns([1.2, 2.0])

# ========== 左侧：设置 ==========

with sidebar:
    st.markdown("### 设置")
    st.caption("（左侧选项仅影响生成的强调方向）")

    refine_focus = st.multiselect(
        "精修侧重（可多选）",
        options=[
            "业务影响",
            "沟通协作",
            "数据分析 / 建模",
            "项目管理",
            "领导力 / 主动性",
            "求职方向：量化 / 咨询 / 科技 / 银行",
        ],
        default=["业务影响"],
    )

    custom_points = st.text_area(
        "增强点（可自定义）",
        value=(
            "例如：强调数据分析/量化成果；突出与目标岗位的匹配；或写作风格要求等…"
        ),
        height=120,
    )

    generate_cover_letter = st.checkbox("生成求职信（Cover Letter）", value=True)

    use_ocr = st.checkbox("启用 OCR（扫描 PDF）", value=False)
    if use_ocr and not HAS_OCR:
        st.info("当前环境未安装 OCR 依赖（pdf2image / pytesseract），将仅使用普通 PDF 文本抽取。")

    st.markdown("---")
    st.caption("仅供个人求职使用，禁止商用与爬取。")


# ========== 中间主区域 ==========

with main_col:
    st.markdown("## 🧠 AI 智能简历优化")

    st.markdown(
        "上传简历，AI 将根据 JD 一键优化；可选生成求职信（Cover Letter，语言自动随简历）。"
    )

    uploaded_file = st.file_uploader(
        "上传简历（PDF 或 DOCX）",
        type=["pdf", "docx"],
        help="支持 PDF / DOCX，单文件 ≤ 50MB；扫描件可启用 OCR。",
        label_visibility="visible",
    )

    st.caption("支持 PDF / DOCX · 单文件 ≤ 50MB · 扫描件可启用 OCR")

with right_col:
    jd_input = st.text_area(
        "粘贴目标职位 JD 或优化指令（可批量、用分隔）",
        value=(
            "例如：Actuarial graduate role at Deloitte. "
            "请重点突出数据分析与建模能力；写作风格正式。Cover Letter 要更正式。"
        ),
        height=200,
    )

# 提示区域 & 一键生成按钮
st.markdown("---")
st.info(
    "💡 提示：可在左侧设置“精修侧重/增强点”；若 PDF 为扫描件，可开启 OCR。"
)

generate_clicked = st.button("🚀 一键生成", use_container_width=True)

# 结果展示区占位
result_cv = None
result_cl = None

if generate_clicked:
    log_event("generate_click")

    if uploaded_file is None:
        st.error("请先上传一份 PDF 或 DOCX 简历。")
    else:
        try:
            # 文件大小检查（50MB）
            file_bytes = uploaded_file.read()
            size_mb = len(file_bytes) / (1024 * 1024)
            if size_mb > 50:
                st.error("上传文件超过 50MB，请压缩或精简后再上传。")
                log_event("file_too_large", {"size_mb": size_mb})
            else:
                suffix = uploaded_file.name.lower().split(".")[-1]

                with st.spinner("正在读取简历内容…"):
                    if suffix == "docx":
                        base_cv_text = read_docx(file_bytes)
                    elif suffix == "pdf":
                        base_cv_text = read_pdf(file_bytes, use_ocr=use_ocr)
                    else:
                        st.error("仅支持 PDF 或 DOCX 文件。")
                        base_cv_text = ""

                if not base_cv_text.strip():
                    st.error("未能从简历中读取到有效文本，请确认文件内容或尝试启用 OCR。")
                    log_event("empty_cv_text", {"filetype": suffix})
                else:
                    lang = detect_language(base_cv_text)
                    cv_prompt, cl_prompt = build_prompt(
                        base_cv=base_cv_text,
                        jd_or_instructions=jd_input,
                        refine_focus=refine_focus,
                        custom_points=custom_points,
                        need_cover_letter=generate_cover_letter,
                        lang=lang,
                    )

                    with st.spinner("AI 正在优化你的简历…"):
                        cv_text = call_openai(cv_prompt, lang)
                        result_cv = cv_text

                    if generate_cover_letter and cl_prompt:
                        with st.spinner("AI 正在撰写求职信…"):
                            cl_text = call_openai(cl_prompt, lang)
                            result_cl = cl_text

                    log_event(
                        "generate_success",
                        {
                            "lang": lang,
                            "has_cover_letter": bool(result_cl),
                            "filetype": suffix,
                        },
                    )

                    # 展示结果 + 提供下载
                    st.markdown("### ✅ 优化后的简历")
                    st.text_area(
                        "预览：优化简历（可复制粘贴）",
                        value=result_cv,
                        height=260,
                    )

                    cv_docx_bytes = export_docx(
                        result_cv,
                        "Optimized_CV.docx",
                    )

                    st.download_button(
                        "⬇️ 下载优化后简历（DOCX）",
                        data=cv_docx_bytes,
                        file_name="optimized_cv.docx",
                        mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    )

                    if result_cl:
                        st.markdown("### 📄 求职信（Cover Letter）")
                        st.text_area(
                            "预览：求职信（可复制粘贴）",
                            value=result_cl,
                            height=220,
                        )
                        cl_docx_bytes = export_docx(
                            result_cl,
                            "Cover_Letter.docx",
                        )
                        st.download_button(
                            "⬇️ 下载求职信（DOCX）",
                            data=cl_docx_bytes,
                            file_name="cover_letter.docx",
                            mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                        )

        except Exception as e:
            log_error("generate_exception", e)
            st.error("生成过程中出现错误，我已经收到错误日志，会尽快修复 🙏")

# ========== 用户反馈区（写在页面最底部） ==========

st.markdown("---")
with st.expander("💬 提交反馈 / 功能建议（可选）"):
    with st.form("user_feedback_form"):
        fb_text = st.text_area(
            "写下你在使用过程中的任何想法：好用的地方 / 有问题的地方 / 希望增加的功能…",
            height=120,
        )
        contact = st.text_input("联系方式（可选）例如邮箱 / 小红书 / 微信号（如不留可以匿名反馈）")
        submitted = st.form_submit_button("提交反馈")
    if submitted:
        if fb_text.strip():
            log_feedback(fb_text, contact)
            st.success("感谢你的反馈，我已经收到，会据此继续优化产品 🙏")
        else:
            st.warning("请先写一点内容再提交～")

# footer
st.caption(
    "© 2025 AI Resume Optimizer | 仅供个人求职使用，禁止商用与爬虫爬取。"
)