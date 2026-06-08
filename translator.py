"""翻译服务模块 - 支持 OpenAI、DeepSeek、Gemini 多翻译引擎，双向翻译
翻译流程：
    Style Config → Terminology Prompt → Memory Retrieval → Prompt Builder → API → Translation
"""

from openai import OpenAI
from config import (
    OPENAI_API_KEY, OPENAI_MODEL,
    DEEPSEEK_API_KEY, DEEPSEEK_MODEL,
    GEMINI_API_KEY, GEMINI_MODEL,
)
from prompt_builder import build_prompt_context
from terminology import build_terminology_prompt
from retriever import retrieve

# ── 翻译方向标签 ──────────────────────────────────

DIRECTION_LABELS = {
    "zh2en": "中 → 英",
    "en2zh": "英 → 中",
}

# ── 延迟初始化客户端（避免导入时因缺 Key 报错）──────

_openai_client = None
_deepseek_client = None
_gemini_client = None


def _get_openai_client():
    global _openai_client
    if _openai_client is None:
        _openai_client = OpenAI(api_key=OPENAI_API_KEY)
    return _openai_client


def _get_deepseek_client():
    global _deepseek_client
    if _deepseek_client is None:
        _deepseek_client = OpenAI(
            api_key=DEEPSEEK_API_KEY,
            base_url="https://api.deepseek.com",
        )
    return _deepseek_client


def _get_gemini_client():
    global _gemini_client
    if _gemini_client is None:
        import google.generativeai as genai
        genai.configure(api_key=GEMINI_API_KEY)
        _gemini_client = genai
    return _gemini_client


# ── 各引擎翻译实现 ─────────────────────────────────

def _translate_openai(text: str, system_prompt: str, model: str = None) -> str:
    client = _get_openai_client()
    response = client.chat.completions.create(
        model=model or OPENAI_MODEL,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": text},
        ],
        temperature=0.3,
    )
    return response.choices[0].message.content.strip()


def _translate_deepseek(text: str, system_prompt: str, model: str = None) -> str:
    client = _get_deepseek_client()
    response = client.chat.completions.create(
        model=model or DEEPSEEK_MODEL,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": text},
        ],
        temperature=0.3,
    )
    return response.choices[0].message.content.strip()


def _translate_gemini(text: str, system_prompt: str, model: str = None) -> str:
    genai = _get_gemini_client()
    gemini_model = genai.GenerativeModel(
        model_name=model or GEMINI_MODEL,
        system_instruction=system_prompt,
    )
    response = gemini_model.generate_content(text)
    return response.text.strip()


# ── 统一入口 ───────────────────────────────────────

TRANSLATORS = {
    "openai": _translate_openai,
    "deepseek": _translate_deepseek,
    "gemini": _translate_gemini,
}

PROVIDER_LABELS = {
    "openai": "OpenAI (GPT-4o)",
    "deepseek": "DeepSeek",
    "gemini": "Gemini",
}


def translate(
    text: str,
    provider: str = "openai",
    direction: str = "en2zh",
    prompt_template: str = "default",
    custom_prompt: str = "",
) -> tuple[str, str]:
    """统一翻译入口（Prompt Builder 中心组装）

    流程：Style Config → Terminology → Memory Retrieval → Prompt Builder → API → Translation

    Args:
        text: 待翻译文本
        provider: 翻译引擎，可选 openai / deepseek / gemini
        direction: 翻译方向，zh2en（中→英）或 en2zh（英→中）
        prompt_template: 模板名 default / political / financial / legal / custom
        custom_prompt: 自定义模板内容（仅 custom 模式生效）

    Returns:
        (translation, memory_context) — 译文 + 检索摘要
    """
    if not text.strip():
        return "", ""

    translator = TRANSLATORS.get(provider)
    if translator is None:
        raise ValueError(f"不支持的翻译引擎: {provider}，可选: {list(TRANSLATORS.keys())}")

    # 1. Terminology → 强制术语统一（术语表为空时自动跳过）
    terminology_prompt = build_terminology_prompt()

    # 2. Memory Retrieval → 从记忆库检索相关资产
    retrieval_result = retrieve(text)
    tm_hits = retrieval_result.get("hits") if retrieval_result else None

    # 3. Prompt Builder → 将各模块结果组装成最终 System Prompt
    extra_principles = None
    if prompt_template == "custom" and custom_prompt.strip():
        extra_principles = [custom_prompt.strip()]

    system_prompt = build_prompt_context(
        source_text=text,
        template_name=prompt_template,
        terminology_prompt=terminology_prompt,
        tm_hits=tm_hits,
        extra_principles=extra_principles,
    )

    # 4. API → 调用翻译引擎
    translation = translator(text, system_prompt)
    return translation, retrieval_result
