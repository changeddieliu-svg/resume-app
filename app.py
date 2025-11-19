import io
import os
from datetime import datetime

import streamlit as st
from openai import OpenAI
from langdetect import detect
import pdfplumber
from docx import Document

# =========================================================
# 0. 页面基础配置 + 隐藏顶部菜单等
# =========================================================

st.set_page_config(
    page_title="AI 智能简历优化",
    page_icon="🧠",
    layout="wide",
)

HIDE_STREAMLIT_STYLE = """
    <style>
    /* 顶部工具栏 / 菜单 / 页脚 */
    [data-testid="stToolbar"] { visibility: hidden; height: 0; position: fixed; }
    [data-testid="stDecoration"] { visibility: hidden; height: 0; }
    #MainMenu {visibility: hidden;}
    footer {visibility: hidden;}
    header {visibility: hidden;}
    </style>
"""
st.markdown(HIDE_STREAMLIT_STYLE, unsafe_allow_html=True)

# =========================================================
# 1. OpenAI 客户端
# =========================================================

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
MODEL_NAME = os.getenv("MODEL_NAME", "gpt-4o-mini")

if not OPENAI_API_KEY:
    st.error("未配置 OPENAI_API_KEY，请在 Streamlit → Settings → Secrets 中添加。")

client = OpenAI()

# =========================================================
# 2. 加载 analytics（Google Sheet 埋点）
# =========================================================

try:
    import analytics  # 你的 analytics.py
    ANALYTICS_AVAILABLE = getattr(analytics, "ANALYTICS_ENABLED", False)
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
        # 不打扰用户，只在后台打印
        print(f"[analytics] log_event 失败: {event_type}, {data}")


# =========================================================
# 3. 工具函数：读取简历 & 生成 DOCX（带简单排版）
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


def create_docx(content: str) -> bytes:
    """
    将模型输出的纯文本转换为带简单排版的 docx：
    - 行首 ### / ## / # 视为标题（加粗）
    - 整行 **xxx** 视为粗体标题（去掉 **）
    - 行首 • / - / * 视为子弹列表
    - 其余为普通段落
    """
    doc = Document()

    lines = content.splitlines()

    for raw_line in lines:
        line = raw_line.rstrip("\n")

        # 空行 -> 空段落（增加留白）
        if not line.strip():
            doc.add_paragraph("")
            continue

        text = line.strip()
        is_heading = False
        is_full_bold = False

        # 1) Markdown 风格标题：### / ## / #
        for prefix in ("### ", "## ", "# "):
            if text.startswith(prefix):
                text = text[len(prefix):].strip()
                is_heading = True
                break

        # 2) 整行 **xxx** 粗体
        if text.startswith("**") and text.endswith("**") and len(text) > 4:
            text = text[2:-2].strip()
            is_full_bold = True

        # 3) 子弹列表：• / - / *
        is_bullet = False
        for bullet_prefix in ("• ", "- ", "* "):
            if text.startswith(bullet_prefix):
                text = text[len(bullet_prefix):].strip()
                is_bullet = True
                break

        # 4) 写入到 docx
        if is_bullet:
            p = doc.add_paragraph(style="List Bullet")
        else:
            p = doc.add_paragraph()

        run = p.add_run(text)

        if is_heading or is_full_bold:
            run.bold = True

    buffer = io.BytesIO()
    doc.save(buffer)
    buffer.seek(0)
    return buffer.getvalue()


# =========================================================
# 4. Prompt 构建 & 调 OpenAI
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
    try:
        return response.output[0].content[0].text
    except Exception:
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
# 5. 初始化 session_state（保证下载后结果不丢）
# =========================================================

if "optimized_resume" not in st.session_state:
    st.session_state["optimized_resume"] = ""
    st.session_state["cover_letter"] = ""
    st.session_state["last_lang"] = "en"
    st.session_state["last_meta"] = {}


# =========================================================
# 6. 页面 UI
# =========================================================

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

    # Analytics 状态展示
    if ANALYTICS_AVAILABLE:
        st.caption("Analytics 状态：✅ 已启用")
    else:
        st.caption("Analytics 状态：⚠️ 已关闭")

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

# 首次打开页面埋点
safe_log_event(
    "page_view",
    {
        "ts": datetime.utcnow().isoformat(),
        "has_file": bool(uploaded_file),
    },
)

# =========================================================
# 7. 一键生成
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

    # 把结果存到 session_state，保证后续点击下载不会丢失
    st.session_state["optimized_resume"] = optimized_resume
    st.session_state["cover_letter"] = cover_letter_text
    st.session_state["last_lang"] = lang
    st.session_state["last_meta"] = {
        "filename": uploaded_file.name,
        "filesize": uploaded_file.size,
        "has_jd": bool(jd_text.strip()),
        "need_cover_letter": need_cover_letter,
    }

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
# 8. 下载区（根据 session_state 显示，不会因为点击按钮丢失）
# =========================================================

if st.session_state["optimized_resume"]:
    st.success("生成完成，你可以下载优化后的简历（以及可选的求职信）。")

    resume_docx_bytes = create_docx(st.session_state["optimized_resume"])
    resume_filename = "Optimized_Resume.docx"
    st.download_button(
        "⬇️ 下载优化简历（DOCX）",
        data=resume_docx_bytes,
        file_name=resume_filename,
        mime=(
            "application/vnd.openxmlformats-officedocument."
            "wordprocessingml.document"
        ),
    )

    if st.session_state["last_meta"].get("need_cover_letter") and st.session_state[
        "cover_letter"
    ].strip():
        cover_docx_bytes = create_docx(st.session_state["cover_letter"])
        cover_filename = "Cover_Letter.docx"
        st.download_button(
            "⬇️ 下载求职信（DOCX）",
            data=cover_docx_bytes,
            file_name=cover_filename,
            mime=(
                "application/vnd.openxmlformats-officedocument."
                "wordprocessingml.document"
            ),
        )
    elif st.session_state["last_meta"].get("need_cover_letter"):
        st.warning("本次模型输出中未识别到有效求职信内容，请检查提示词或重新生成。")

# =========================================================
# 9. 用户反馈入口
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