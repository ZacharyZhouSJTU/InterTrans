"""翻译服务模块 - 支持 OpenAI、DeepSeek、Gemini 多翻译引擎，双向翻译
翻译流程：
    Style Config → Terminology Prompt → Runtime Terminology → Memory Retrieval → Prompt Builder → API → Translation

本版新增：
    - 支持 app.py 传入 domain / matched_terms / system_prompt；
    - 将 Streamlit 页面命中的术语真正注入 System Prompt；
    - 保留原有术语表、翻译记忆检索和 prompt_builder 流程。
"""

from __future__ import annotations

from typing import Any, Iterable

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


def _require_openai_sdk():
    """延迟导入 openai SDK，避免 app 启动阶段因依赖缺失而打不开网页。"""
    try:
        from openai import OpenAI
        return OpenAI
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "缺少 openai SDK。请在 requirements.txt 中确认包含 openai>=1.0.0，"
            "然后重新部署；本地可运行：pip install openai"
        ) from exc


def _get_openai_client():
    global _openai_client
    if _openai_client is None:
        OpenAI = _require_openai_sdk()
        _openai_client = OpenAI(api_key=OPENAI_API_KEY)
    return _openai_client


def _get_deepseek_client():
    global _deepseek_client
    if _deepseek_client is None:
        OpenAI = _require_openai_sdk()
        _deepseek_client = OpenAI(
            api_key=DEEPSEEK_API_KEY,
            base_url="https://api.deepseek.com",
        )
    return _deepseek_client


def _get_gemini_client():
    global _gemini_client
    if _gemini_client is None:
        try:
            import google.generativeai as genai
        except ModuleNotFoundError as exc:
            raise RuntimeError(
                "缺少 google-generativeai。请在 requirements.txt 中确认包含 "
                "google-generativeai>=0.8.0，然后重新部署；本地可运行：pip install google-generativeai"
            ) from exc
        genai.configure(api_key=GEMINI_API_KEY)
        _gemini_client = genai
    return _gemini_client


# ── 各引擎翻译实现 ─────────────────────────────────

def _translate_openai(text: str, system_prompt: str, model: str | None = None) -> str:
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


def _translate_deepseek(text: str, system_prompt: str, model: str | None = None) -> str:
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


def _translate_gemini(text: str, system_prompt: str, model: str | None = None) -> str:
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


# ═══════════════════════════════════════════════════════
# 运行期术语注入工具函数
# ═══════════════════════════════════════════════════════

def _safe_strip(value: Any) -> str:
    return str(value or "").strip()


def _coerce_term_pairs(
    matched_terms: Iterable[Any] | dict[str, Any] | None = None,
    terminology: Iterable[Any] | dict[str, Any] | None = None,
) -> list[tuple[str, str]]:
    """把 app.py 传入的术语统一转成 [(中文, English), ...]。兼容 tuple/list/dict。"""
    raw_items: list[Any] = []

    def extend_from(obj: Any) -> None:
        if not obj:
            return
        if isinstance(obj, dict):
            # 兼容 {domain: [(zh, en), ...]} 或 {"source_text": ..., "target_text": ...}
            if any(k in obj for k in ("source_text", "target_text", "source", "target", "zh", "en")):
                raw_items.append(obj)
            else:
                for values in obj.values():
                    if isinstance(values, (list, tuple, set)):
                        raw_items.extend(values)
                    elif values:
                        raw_items.append(values)
        elif isinstance(obj, (list, tuple, set)):
            raw_items.extend(obj)
        else:
            raw_items.append(obj)

    extend_from(matched_terms)
    extend_from(terminology)

    result: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()

    for item in raw_items:
        source = target = ""
        if isinstance(item, dict):
            source = _safe_strip(
                item.get("source_text")
                or item.get("source")
                or item.get("zh")
                or item.get("ch")
                or item.get("cn")
                or item.get("中文")
            )
            target = _safe_strip(
                item.get("target_text")
                or item.get("target")
                or item.get("en")
                or item.get("english")
                or item.get("英文")
            )
        elif isinstance(item, (list, tuple)) and len(item) >= 2:
            source = _safe_strip(item[0])
            target = _safe_strip(item[1])

        if not source or not target:
            continue
        key = (source.lower(), target.lower())
        if key in seen:
            continue
        seen.add(key)
        result.append((source, target))

    return result


def _build_runtime_terminology_prompt(
    term_pairs: list[tuple[str, str]],
    *,
    domain: str | None = None,
    direction: str = "en2zh",
) -> str:
    """根据当前句子实际命中的术语生成强约束提示。"""
    if not term_pairs:
        return ""

    direction_label = DIRECTION_LABELS.get(direction, direction)
    term_lines = "\n".join(f"- {source} ↔ {target}" for source, target in term_pairs)

    return f"""# Runtime Terminology Rules
以下术语来自当前 Streamlit 页面术语库，并且已经在当前待翻译文本中命中。

- 当前领域：{domain or "自动检测/未指定"}
- 翻译方向：{direction_label}
- 必须严格遵循术语对照表；不得随意同义替换、漏译或改变专名。
- 中译英时优先使用右侧英文；英译中时优先使用左侧中文。
- 如果上下文确实要求变形，可以做必要的单复数、大小写或语法适配，但术语核心表达必须保持一致。

## Term List
{term_lines}
""".strip()


def _join_prompt_blocks(*blocks: str | None) -> str:
    """拼接 prompt，自动跳过空块。"""
    return "\n\n".join(block.strip() for block in blocks if block and block.strip())


def _normalize_retrieval_result(retrieval_result: Any) -> dict[str, Any]:
    """确保 retrieval 结果一定是 dict，便于 app.py 展示。"""
    if isinstance(retrieval_result, dict):
        result = dict(retrieval_result)
    elif retrieval_result:
        result = {"raw": retrieval_result}
    else:
        result = {}
    result.setdefault("hits", [])
    result.setdefault("count", len(result.get("hits") or []))
    return result


def translate(
    text: str,
    provider: str = "openai",
    direction: str = "en2zh",
    prompt_template: str = "default",
    custom_prompt: str = "",
    *,
    domain: str | None = None,
    matched_terms: Iterable[Any] | dict[str, Any] | None = None,
    terminology: Iterable[Any] | dict[str, Any] | None = None,
    system_prompt: str | None = None,
    term_prompt: str | None = None,
    return_prompt: bool = False,
    **kwargs: Any,
) -> tuple[str, dict[str, Any]]:
    """统一翻译入口（Prompt Builder 中心组装）

    流程：Style Config → Static Terminology → Runtime Terminology → Memory Retrieval
        → Prompt Builder → API → Translation

    Args:
        text: 待翻译文本。
        provider: 翻译引擎，可选 openai / deepseek / gemini。
        direction: 翻译方向，zh2en（中→英）或 en2zh（英→中）。
        prompt_template: 模板名 default / political / financial / legal / custom。
        custom_prompt: 自定义模板内容（仅 custom 模式生效）。
        domain: app.py 检测或用户选择的领域。
        matched_terms: app.py 在当前文本中命中的术语，例如 [("人工智能", "artificial intelligence")]。
        terminology: 兼容字段；可传入术语列表或 {domain: terms}。
        system_prompt: app.py 基于领域和术语生成的 Prompt，会被加入最终 System Prompt。
        term_prompt: 兼容字段；等价于额外的术语/领域 Prompt。
        return_prompt: 调试用。True 时会把 final_system_prompt 放入 retrieval 结果。
        **kwargs: 兼容未来 app.py 传入的额外字段，当前会忽略。

    Returns:
        (translation, retrieval_result) — 译文 + 检索/术语注入摘要。
    """
    if not text.strip():
        return "", {}

    translator = TRANSLATORS.get(provider)
    if translator is None:
        raise ValueError(f"不支持的翻译引擎: {provider}，可选: {list(TRANSLATORS.keys())}")

    # 1. Static Terminology → 保留原有全局术语表逻辑
    try:
        static_terminology_prompt = build_terminology_prompt()
    except Exception as exc:
        # 术语表读取失败不应中断翻译，但要把错误返回给页面便于排查
        static_terminology_prompt = ""
        terminology_error = str(exc)
    else:
        terminology_error = ""

    # 2. Runtime Terminology → 接收 app.py 当前句子命中的术语
    term_pairs = _coerce_term_pairs(matched_terms=matched_terms, terminology=terminology)
    runtime_terminology_prompt = _build_runtime_terminology_prompt(
        term_pairs,
        domain=domain,
        direction=direction,
    )

    # 3. Memory Retrieval → 从记忆库检索相关资产
    raw_retrieval_result = retrieve(text)
    retrieval_result = _normalize_retrieval_result(raw_retrieval_result)
    tm_hits = retrieval_result.get("hits") or None

    # 4. Prompt Builder → 将各模块结果组装成基础 System Prompt
    extra_principles: list[str] = []
    if prompt_template == "custom" and custom_prompt.strip():
        extra_principles.append(custom_prompt.strip())

    combined_terminology_prompt = _join_prompt_blocks(
        static_terminology_prompt,
        runtime_terminology_prompt,
    )

    base_system_prompt = build_prompt_context(
        source_text=text,
        template_name=prompt_template,
        terminology_prompt=combined_terminology_prompt,
        tm_hits=tm_hits,
        extra_principles=extra_principles or None,
    )

    # 5. App Injected Prompt → 把 app.py 生成的领域/术语 Prompt 放到最前面，优先级更高
    injected_prompt = _join_prompt_blocks(system_prompt, term_prompt)
    final_system_prompt = _join_prompt_blocks(
        injected_prompt,
        "# Base Translation Prompt\n" + base_system_prompt,
    )

    # 6. API → 调用翻译引擎
    translation = translator(text, final_system_prompt)

    # 7. 给 app.py 回传可展示的术语和检索状态
    retrieval_result.update({
        "domain": domain or retrieval_result.get("domain") or "其他",
        "matched_terms": term_pairs,
        "term_prompt": injected_prompt or runtime_terminology_prompt,
        "runtime_terminology_prompt": runtime_terminology_prompt,
        "used_term_prompt": bool(injected_prompt or runtime_terminology_prompt),
        "translator_needs_patch": False,
    })
    if terminology_error:
        retrieval_result["terminology_error"] = terminology_error
    if return_prompt:
        retrieval_result["final_system_prompt"] = final_system_prompt

    return translation, retrieval_result
