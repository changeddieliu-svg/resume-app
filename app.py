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

# 隐藏工具栏 / 菜单 / 页眉页脚
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
# 2. 工具函数：读取简历 & 生成 DOCX（带 Markdown 清洗）
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


# ---------- Markdown → docx 的简易解析 ----------

def _add_formatted_runs(paragraph, text: str):
    """
    把一行里的 **粗体** / *斜体* 从 markdown 标记转成真正的格式。
    不支持嵌套太复杂的情况，但足够覆盖简历场景。
    """
    i = 0
    bold = False
    italic = False
    buf = []

    def flush():
        if buf:
            run = paragraph.add_run("".join(buf))
            run.bold = bold
            run.italic = italic
            buf.clear()

    while i < len(text):
        # 粗体标记 **
        if text[i:i+2] == "**":
            flush()
            bold = not bold
            i += 2
        # 斜体标记 *
        elif text[i] == "*":
            flush()
            italic = not italic
            i += 1
        else:
            buf.append(text[i])
            i += 1
    flush()


def create_docx_from_markdown(content: str) -> bytes:
    """
    把模型生成的 markdown 风格文本，尽量转换成：
    - 正常段落
    - ● bullet 列表
    - **粗体** / *斜体* 变成真正格式
    - 行首的 ### / ## / # 识别为标题（Heading 3/2/1）
    同时去掉实际文本里的 ** / * / ### 等符号。
    """
    doc = Document()

    lines = content.splitlines()

    for raw_line in lines:
        line = raw_line.rstrip("\n")

        # 空行 -> 空段落
        if not line.strip():
            doc.add_paragraph("")  # 作为间距
            continue

        stripped = line.lstrip()

        # 1) 处理标题（以 # 开头）
        heading_level = None
        if stripped.startswith("#"):
            # 统计最前面的 # 数量
            hash_count = 0
            for ch in stripped:
                if ch == "#":
                    hash_count += 1
                else:
                    break
            # 只在后面有空格时认定为标题
            rest = stripped[hash_count:].lstrip()
            if rest:
                heading_level = min(3, max(1, hash_count))
                text_for_para = rest
        else:
            text_for_para = stripped

        # 2) 处理 bullet 列表
        is_bullet = False
        bullet_prefixes = ["- ", "* ", "• "]
        for prefix in bullet_prefixes:
            if text_for_para.startswith(prefix):
                is_bullet = True
                text_for_para = text_for_para[len(prefix):].lstrip()
                break

        # 3) 创建段落：标题优先，其次 bullet，最后普通段落
        if heading_level is not None:
            para_style = f"Heading {heading_level}"
            para = doc.add_paragraph(style=para_style)
        elif is_bullet:
            # Word 自带的 bullet 样式
            para = doc.add_paragraph(style="List Bullet")
        else:
            para = doc.add_paragraph(style="Normal")

        # 4) 把 ** / * 的 markdown 标记转成格式
        _add_formatted_runs(para, text_for_para)

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

# 初始化 session_state，避免下载后结果消失
if "optimized_resume" not in st.session_state:
    st.session_state.optimized_resume = None
    st.session_state.cover_letter_text = None
    st.session_state.lang = None
    st.session_state.meta = {}

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

    analytics_status = "已启用 ✅" if ANALYTICS_AVAILABLE else "已关闭 ⚠️"
    st.markdown(f"**Analytics 状态：** {analytics_status}")

    st.caption("仅供个人求职使用，禁止商用与爬取。")

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
            "例如：Actuarial graduate role at Deloitte。"
            "可以直接写 JD，也可以写优化指令，例如："
            "‘请重点突出数据分析与建模能力；Cover Letter 要更正式’。"
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
# 5. 主按钮：一键生成（写入 session_state）
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

    # 把结果写入 session_state，防止下载时丢失
    st.session_state.optimized_resume = optimized_resume
    st.session_state.cover_letter_text = cover_letter_text
    st.session_state.lang = lang
    st.session_state.meta = {
        "filename": uploaded_file.name,
        "filesize": uploaded_file.size,
        "has_jd": bool(jd_text.strip()),
        "need_cover_letter": need_cover_letter,
    }

    # 埋点
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
# 6. 结果展示 & 下载（基于 session_state）
# =========================================================

if st.session_state.optimized_resume:
    optimized_resume = st.session_state.optimized_resume
    cover_letter_text = st.session_state.cover_letter_text
    lang = st.session_state.lang
    meta = st.session_state.meta

    st.success("生成完成，你可以下载优化后的简历（以及可选的求职信）。")

    # 生成 DOCX（带 bullet + ** 去除 + 标题处理）
    resume_docx_bytes = create_docx_from_markdown(optimized_resume)
    resume_filename = "Optimized_Resume.docx"
    st.download_button(
        "⬇️ 下载优化简历（DOCX）",
        data=resume_docx_bytes,
        file_name=resume_filename,
        mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )

    if meta.get("need_cover_letter") and cover_letter_text and cover_letter_text.strip():
        cover_docx_bytes = create_docx_from_markdown(cover_letter_text)
        cover_filename = "Cover_Letter.docx"
        st.download_button(
            "⬇️ 下载求职信（DOCX）",
            data=cover_docx_bytes,
            file_name=cover_filename,
            mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        )
    elif meta.get("need_cover_letter"):
        st.warning("本次模型输出中未识别到有效求职信内容，请检查提示词或重新生成。")

# =========================================================
# 7. 用户反馈入口
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