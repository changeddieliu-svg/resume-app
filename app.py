import io
import os
from datetime import datetime

import streamlit as st
from openai import OpenAI
from langdetect import detect
import pdfplumber
from docx import Document

# =========================================================
# 1. 基础配置 & 安全地加载 analytics（可选）
# =========================================================

st.set_page_config(
    page_title="AI 智能简历优化",
    page_icon="🧠",
    layout="wide",
)

# 隐藏右上角的 “View code / Rerun” 菜单，避免普通用户看到源码
HIDE_STREAMLIT_STYLE = """
    <style>
    [data-testid="stToolbar"] { visibility: hidden; height: 0; position: fixed; }
    [data-testid="stDecoration"] { visibility: hidden; height: 0; }
    #MainMenu {visibility: hidden;}
    footer {visibility: hidden;}
    header {visibility: hidden;}
    </style>
"""
st.markdown(HIDE_STREAMLIT_STYLE, unsafe_allow_html=True)

# ---- OpenAI 客户端 ----
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
MODEL_NAME = os.getenv("MODEL_NAME", "gpt-4o-mini")

if not OPENAI_API_KEY:
    st.error("未配置 OPENAI_API_KEY，请在 Streamlit → Settings → Secrets 中添加。")
client = OpenAI()

# ---- 安全加载 analytics（Google Sheet） ----
try:
    import analytics  # 你自己的 analytics.py

    ANALYTICS_AVAILABLE = True
except Exception:
    analytics = None
    ANALYTICS_AVAILABLE = False


def safe_log_event(event_type: str, data: dict):
    """所有埋点都通过这里调用，避免影响主流程"""
    if not ANALYTICS_AVAILABLE:
        return
    try:
        analytics.log_event(event_type, data)
    except Exception:
        # 不在 UI 中打扰用户，只是静默失败
        pass


# =========================================================
# 2. 工具函数：读取简历 & 生成 DOCX（带 bullet 样式）
# =========================================================

def read_docx(file_bytes: bytes) -> str:
    buffer = io.BytesIO(file_bytes)
    doc = Document(buffer)
    texts = []
    for para in doc.paragraphs:
        if para.text.strip():
            texts.append(para.text.strip())
    return "\n".join(texts)


def read_pdf(file_bytes: bytes) -> str:
    buffer = io.BytesIO(file_bytes)
    texts = []
    with pdfplumber.open(buffer) as pdf:
        for page in pdf.pages:
            try:
                t = page.extract_text() or ""
            except Exception:
                t = ""
            if t.strip():
                texts.append(t.strip())
    return "\n\n".join(texts)


def extract_resume_text(uploaded_file, enable_ocr: bool) -> str:
    """根据文件类型提取文本；OCR 目前只给提示，不做真正识别"""
    suffix = (uploaded_file.name or "").lower()

    file_bytes = uploaded_file.read()
    # 读完要复位，不然后面再读会是空
    uploaded_file.seek(0)

    if suffix.endswith(".docx"):
        return read_docx(file_bytes)
    elif suffix.endswith(".pdf"):
        text = read_pdf(file_bytes)
        if not text.strip() and enable_ocr:
            st.warning("检测到 PDF 可能是扫描件，目前版本尚未接入 OCR 引擎，先按空文本处理。")
        return text
    else:
        st.error("目前仅支持 PDF 或 DOCX 文件。")
        return ""


def create_docx_with_lists(content: str) -> bytes:
    """
    将模型输出的纯文本写入 DOCX：
    - 识别以 - / * / • 开头的行为无序列表（bullet）
    - 识别简单的 1. / 2) 这种为有序列表
    - 其它按普通段落处理
    """
    from docx.enum.style import WD_STYLE_TYPE  # 以防有些模板没有 List Bullet / List Number

    doc = Document()

    # 确保有列表样式（大多数模板自带，这里只是兜底不报错）
    styles = doc.styles
    has_bullet = any(
        s.type == WD_STYLE_TYPE.PARAGRAPH and s.name == "List Bullet" for s in styles
    )
    has_number = any(
        s.type == WD_STYLE_TYPE.PARAGRAPH and s.name == "List Number" for s in styles
    )

    lines = content.splitlines()

    for line in lines:
        raw = line.rstrip("\n")
        stripped = raw.strip()

        # 空行 -> 空段落，做分段
        if not stripped:
            doc.add_paragraph("")
            continue

        # 简单检测 bullet
        bullet_prefixes = ("- ", "* ", "• ", "· ")
        is_bullet = any(stripped.startswith(p) for p in bullet_prefixes)

        # 简单检测有序列表：1. xxx / 2) xxx
        is_numbered = False
        if stripped[0].isdigit():
            if len(stripped) > 2 and stripped[1:3] in (". ", ") "):
                is_numbered = True

        if is_bullet and has_bullet:
            # 去掉前缀里的符号
            for p in bullet_prefixes:
                if stripped.startswith(p):
                    text = stripped[len(p):].strip()
                    break
            else:
                text = stripped
            p = doc.add_paragraph(text)
            p.style = "List Bullet"
        elif is_numbered and has_number:
            # 截掉前面的 "1. " / "2) "
            first_space = stripped.find(" ")
            if first_space != -1:
                text = stripped[first_space + 1:].strip()
            else:
                text = stripped
            p = doc.add_paragraph(text)
            p.style = "List Number"
        else:
            # 普通段落
            doc.add_paragraph(stripped)

    buffer = io.BytesIO()
    doc.save(buffer)
    buffer.seek(0)
    return buffer.getvalue()


# =========================================================
# 3. Prompt 构建 & 调 OpenAI
# =========================================================

def detect_language(text: str) -> str:
    try:
        lang = detect(text[:1000])
    except Exception:
        lang = "en"
    if lang.startswith("zh"):
        return "zh"
    return "en"


def build_prompt(
    resume_text: str,
    jd_text: str,
    focus_tags: list,
    extra_points: str,
    need_cover_letter: bool,
    lang: str,
) -> str:
    lang_label = "中文" if lang == "zh" else "英文"

    focus_str = "、".join(focus_tags) if focus_tags else "通用求职能力"
    extra_str = extra_points.strip() or "按照目标岗位和简历内容进行专业优化。"

    cover_tip = (
        "同时生成一封匹配该岗位的求职信（Cover Letter）。"
        if need_cover_letter
        else "不需要生成求职信，只优化简历本身。"
    )

    jd_part = jd_text.strip() or "未提供详细 JD，只根据简历内容做通用优化。"

    prompt = f"""
你是一名专业的人才招聘与职业发展顾问，擅长为{lang_label}简历做深度优化。
请根据【候选人原始简历】和【目标岗位/优化指令】，输出：

1. 一份结构清晰、可直接投递的{lang_label}简历文本；
2. {cover_tip}
3. 保持内容真实性，不虚构经历或技能；
4. 保留尽可能多的关键细节，但允许优化表述方式；
5. 尽量量化成绩（例如用百分比、金额、规模等）；
6. 严格避免任何水印、阅读说明或“由 AI 生成”的字样，只输出真实可用内容；
7. 输出语言必须与【候选人原始简历】一致（本次应为：{lang_label}）。

本次精修重点包括（但不限于）：{focus_str}。
你还需要特别注意：{extra_str}

请按照下面的输出格式组织结果（注意分隔标记）：

==== 优化后简历 START ====
（这里是可以直接复制到 Word 里的完整{lang_label}简历）
==== 优化后简历 END ====

==== 求职信 START ====
（如果需要求职信，则输出完整{lang_label}求职信；如果用户不需要求职信，请留空）
==== 求职信 END ====

-----------------------
【候选人原始简历】
{resume_text}

-----------------------
【目标岗位 / 优化指令】
{jd_part}
"""
    return prompt


def call_openai(prompt: str) -> str:
    response = client.responses.create(
        model=MODEL_NAME,
        input=prompt,
    )
    # 新版 Responses API：取第一段文本
    try:
        return response.output[0].content[0].text
    except Exception:
        # 兜底：直接转成字符串
        return str(response)


def parse_model_output(raw: str):
    """根据约定的分隔符切分出简历 & 求职信"""
    resume = ""
    cover = ""

    if "==== 优化后简历 START ====" in raw:
        try:
            part = raw.split("==== 优化后简历 START ====")[1]
            part = part.split("==== 优化后简历 END ====")[0]
            resume = part.strip()
        except Exception:
            resume = raw.strip()
    else:
        resume = raw.strip()

    if "==== 求职信 START ====" in raw:
        try:
            part = raw.split("==== 求职信 START ====")[1]
            part = part.split("==== 求职信 END ====")[0]
            cover = part.strip()
        except Exception:
            cover = ""

    return resume, cover


# =========================================================
# 4. 页面 UI
# =========================================================

# ---- 左侧设置栏 ----
with st.sidebar:
    st.title("设置")

    st.caption("（左侧选项仅影响生成的强调方向）")

    focus_options = [
        "业务影响",
        "沟通协作",
        "领导力/Ownership",
        "项目管理",
        "数据驱动、可量化",
        "关键字契合度（ATS 友好）",
    ]
    focus_tags = st.multiselect(
        "精修侧重（可多选）",
        options=focus_options,
        default=["业务影响"],
    )

    extra_points = st.text_area(
        "增强点（可自定义）",
        value="例如：强调数据分析/量化成果；突出与目标岗位的匹配；或写作风格要求等…",
        height=120,
    )

    need_cover_letter = st.checkbox("✉️ 生成求职信（Cover Letter）", value=True)
    enable_ocr = st.checkbox("🔍 启用 OCR（扫描 PDF）", value=False)

    st.markdown("---")
    st.caption("仅供个人求职使用，禁止商用与爬取。")

    # 显示 Analytics 状态，方便你确认是否写入 Google Sheet
    if ANALYTICS_AVAILABLE:
        st.caption("📊 Analytics：**已启用**（数据写入 Google Sheet）")
    else:
        st.caption("📊 Analytics：**未启用**（当前不记录使用数据）")

# ---- 页面标题 ----
st.markdown("## 🧠 AI 智能简历优化")

col_left, col_right = st.columns(2, gap="large")

with col_left:
    st.subheader("上传简历（PDF 或 DOCX）")
    uploaded_file = st.file_uploader(
        "", type=["pdf", "docx"], label_visibility="collapsed"
    )
    st.caption("支持 PDF / DOCX，单文件 ≤ 50MB；扫描件可启用 OCR。")

with col_right:
    st.subheader("粘贴目标职位 JD 或优化指令（可批量、用分隔）")
    jd_text = st.text_area(
        "",
        placeholder=(
            "例如：Actuarial graduate role at Deloitte。\n"
            "可以直接贴 JD，也可以写优化指令，例如：\n"
            "“请重点突出数据分析与建模能力；Cover Letter 要更正式”。"
        ),
        height=180,
        label_visibility="collapsed",
    )

st.info("💡 提示：可在左侧设置“精修侧重/增强点”；若 PDF 为扫描件，可开启 OCR。")

# ---- 首次打开页面的埋点 ----
safe_log_event(
    "page_view",
    {
        "ts": datetime.utcnow().isoformat(),
        "has_file": bool(uploaded_file),
    },
)

# =========================================================
# 5. 主按钮：一键生成
# =========================================================

generate_btn = st.button("🚀 一键生成", use_container_width=True)

if generate_btn:
    if not uploaded_file:
        st.error("请先上传简历文件（PDF 或 DOCX）。")
        st.stop()

    if uploaded_file.size and uploaded_file.size > 50 * 1024 * 1024:
        st.error("文件超过 50MB，请压缩后重新上传。")
        st.stop()

    with st.spinner("正在读取简历并调用 AI 优化，请稍候…"):
        resume_text = extract_resume_text(uploaded_file, enable_ocr)

        if not resume_text.strip():
            st.error("未能从简历中提取文本，请确认文件是否为可复制文本。")
            st.stop()

        lang = detect_language(resume_text)

        prompt = build_prompt(
            resume_text=resume_text,
            jd_text=jd_text,
            focus_tags=focus_tags,
            extra_points=extra_points,
            need_cover_letter=need_cover_letter,
            lang=lang,
        )

        raw_output = call_openai(prompt)
        optimized_resume, cover_letter_text = parse_model_output(raw_output)

    # ===== 下载区 =====
    st.success("生成完成，你可以下载优化后的简历（以及可选的求职信）。")

    resume_docx_bytes = create_docx_with_lists(optimized_resume)
    resume_filename = "Optimized_Resume.docx"
    st.download_button(
        "⬇️ 下载优化简历（DOCX）",
        data=resume_docx_bytes,
        file_name=resume_filename,
        mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )

    if need_cover_letter and cover_letter_text.strip():
        cover_docx_bytes = create_docx_with_lists(cover_letter_text)
        cover_filename = "Cover_Letter.docx"
        st.download_button(
            "⬇️ 下载求职信（DOCX）",
            data=cover_docx_bytes,
            file_name=cover_filename,
            mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        )
    elif need_cover_letter:
        st.warning("本次模型输出中未识别到有效求职信内容，请检查提示词或重新生成。")

    # 记录生成事件
    safe_log_event(
        "generate",
        {
            "ts": datetime.utcnow().isoformat(),
            "filename": uploaded_file.name,
            "filesize": uploaded_file.size,
            "lang": lang,
            "has_jd": bool(jd_text.strip()),
            "need_cover_letter": need_cover_letter,
        },
    )

# =========================================================
# 6. 用户反馈入口
# =========================================================

st.markdown("---")
feedback = st.text_area(
    "💬 提交反馈 / 功能建议（可选）",
    placeholder="例如：哪里用得不顺手？希望增加什么功能？或者遇到了什么错误？",
    height=100,
)

if st.button("📨 提交反馈", use_container_width=False):
    if not feedback.strip():
        st.warning("请先填写一些反馈内容，再点击提交。")
    else:
        safe_log_event(
            "user_feedback",
            {
                "ts": datetime.utcnow().isoformat(),
                "feedback": feedback.strip(),
            },
        )
        st.success("谢谢你的反馈！我会根据这些建议持续优化产品。")