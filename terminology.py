"""Terminology — 强制术语统一，通过 Prompt 注入翻译术语表"""

import json
import os

_TERM_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "terminology.json")


def load_terminology() -> dict[str, str]:
    """加载术语表，返回 {source_term: target_term}。文件为空或不存在时返回 {}"""
    if not os.path.exists(_TERM_PATH):
        return {}
    try:
        with open(_TERM_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return {k.strip(): v.strip() for k, v in data.items() if k.strip() and v.strip()}
        return {}
    except (json.JSONDecodeError, IOError):
        return {}


def build_terminology_prompt() -> str:
    """
    构造术语约束 Prompt。

    如果术语表为空，返回空字符串（下游自动跳过）。
    格式示例：

        ## Terminology (must use these translations)
        - LLM → 大语言模型
        - RAG → 检索增强生成
    """
    terms = load_terminology()
    if not terms:
        return ""

    lines = ["## Terminology (must use these translations)"]
    for src, tgt in terms.items():
        lines.append(f"- {src} → {tgt}")

    return "\n".join(lines)
