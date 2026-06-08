"""Retriever — 从 Memory Base 检索相关翻译资产（关键词匹配）"""

import re
from database import get_connection


def _tokenize(text: str) -> set[str]:
    if not text:
        return set()
    return set(re.findall(r"[a-zA-Z0-9一-鿿]+", str(text).lower()))


def retrieve(source_text: str, limit: int = 10) -> dict:
    """
    从 memory_assets 中检索与 source_text 相关的全部资产。

    返回:
    {
        "hits": [{"source_text": ..., "target_text": ...}, ...],
        "count": N,
    }
    """
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM memory_assets WHERE status='active' ORDER BY updated_time DESC"
    ).fetchall()
    conn.close()

    input_tokens = _tokenize(source_text)
    hits = []

    for r in rows:
        asset = dict(r)
        asset_tokens = _tokenize(asset["source_text"])
        if input_tokens & asset_tokens:
            hits.append({
                "source_text": asset["source_text"],
                "target_text": asset["target_text"],
            })
            if len(hits) >= limit:
                break

    return {"hits": hits, "count": len(hits)}
