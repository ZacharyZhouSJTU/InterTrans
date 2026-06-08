"""Prompt Builder — 将各模块结果组装成最终 PromptContext

职责：
    作为翻译记忆学习系统的中心组装器，将领域识别、风格模板、术语表、
    翻译记忆检索、风格示例等模块的输出组装成结构化的最终 Prompt。

组装结构：
    Domain:          {自动识别}
    Target Audience: {风格配置的受众}
    Style:           {模板名称}
    Terminology:     {术语内容，可选}
    Translation Memory: {TM 检索结果，可选}
    Style Examples:  {风格示例，可选}
    Translation Principles: {翻译原则}
"""

from typing import Optional

from retriever import retrieve
from prompt_center import detect_domain, get_style_config, get_effective_principles


def build_prompt_context(
    source_text: str,
    template_name: str = "default",
    terminology_prompt: str = "",
    tm_hits: Optional[list[dict]] = None,
    style_examples: Optional[str] = None,
    extra_principles: Optional[list[str]] = None,
) -> str:
    """
    将各模块结果组装成最终 System Prompt。

    Args:
        source_text:         待翻译的原文
        template_name:       模板名（default / political / financial / legal / custom）
        terminology_prompt:  terminology.build_terminology_prompt() 的输出
        tm_hits:             retriever.retrieve() 返回的 hits 列表
        style_examples:      可选的风格示例文本（来自风格库或手动提供）
        extra_principles:    额外的翻译原则（如来自用户自定义）

    Returns:
        结构化 System Prompt 字符串，供 LLM 使用
    """
    config = get_style_config(template_name)

    # ── 1. Domain（自动识别）──────────────────────────
    domain = detect_domain(source_text)

    # ── 2. Target Audience ─────────────────────────────
    audience = config.get("audience", "Professional Readers")

    # ── 3. Style ───────────────────────────────────────
    style_name = config.get("style_name", template_name)

    # ── 4-7. 构建各部分 ────────────────────────────────
    sections: list[str] = []

    # Domain
    sections.append(f"## Domain\n{domain}")

    # Target Audience
    sections.append(f"## Target Audience\n{audience}")

    # Style
    sections.append(f"## Style\n{style_name}")

    # Terminology（术语表为空时跳过）
    if terminology_prompt:
        # terminology_prompt 已包含 "## Terminology" 标题，直接使用内容
        sections.append(terminology_prompt)

    # Translation Memory（TM 为空时跳过）
    if tm_hits:
        tm_lines = ["## Translation Memory"]
        for h in tm_hits:
            tm_lines.append(f"- {h['source_text']} → {h['target_text']}")
        sections.append("\n".join(tm_lines))

    # Style Examples（为空时跳过）
    if style_examples:
        sections.append(f"## Style Examples\n{style_examples}")

    # Translation Principles（优先使用用户覆写）
    principles = get_effective_principles(template_name)
    if extra_principles:
        principles.extend(extra_principles)
    if principles:
        principles_lines = ["## Translation Principles"]
        for i, p in enumerate(principles, 1):
            principles_lines.append(f"{i}. {p}")
        sections.append("\n".join(principles_lines))

    # ── 组装 ───────────────────────────────────────────
    return "\n\n".join(sections)


def build_system_prompt(source_text: str, base_prompt: str = "") -> tuple[str, dict]:
    """
    以 base_prompt 为基础，叠加从 Memory Base 检索到的资产。

    兼容旧接口，内部调用 build_prompt_context。

    Returns:
        (system_prompt, retrieval_result)
    """
    if not base_prompt:
        base_prompt = "You are a professional translator."

    result = retrieve(source_text)

    # 使用新的组装逻辑构建完整 System Prompt
    # base_prompt 作为翻译原则的补充
    system_prompt = build_prompt_context(
        source_text=source_text,
        template_name="default",
        tm_hits=result.get("hits") if result else None,
        extra_principles=[base_prompt] if base_prompt else None,
    )

    # 追加翻译指令
    system_prompt += "\n\n---\nTranslate the following text according to the above specifications."

    return system_prompt, result
