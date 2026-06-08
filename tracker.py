"""修改追踪模块 - 检测重复修改，生成候选规则，支持确认/忽略/稍后"""

from database import save_modification, upsert_candidate_rule, update_rule_status

REPEAT_THRESHOLD = 3  # 相同修改出现 N 次后触发提示


def record_modification(source: str, original: str, modified: str) -> dict:
    """
    记录一次修改，检测是否需要生成候选规则。

    返回格式:
    {
        "should_prompt": bool,       # 是否应该弹出提示
        "rule_id": int | None,       # 候选规则 ID
        "count": int,                # 当前相同修改次数
        "original_phrase": str,      # 修改前（GPT 译文）
        "modified_phrase": str,      # 修改后（人工译文）
        "status": str | None,        # 规则当前状态
    }
    """
    # 如果没有实际修改，不记录
    if original.strip() == modified.strip():
        return {
            "should_prompt": False,
            "rule_id": None,
            "count": 0,
            "original_phrase": original,
            "modified_phrase": modified,
            "status": None,
        }

    # 保存修改记录到历史
    save_modification(source, original, modified)

    # 创建或更新候选规则
    rule = upsert_candidate_rule(original, modified)

    if rule is None:
        # 规则已被忽略
        return {
            "should_prompt": False,
            "rule_id": None,
            "count": 0,
            "original_phrase": original,
            "modified_phrase": modified,
            "status": "ignored",
        }

    # 达到阈值且状态为 pending 或 later 时才提示
    should_prompt = (
        rule["count"] >= REPEAT_THRESHOLD
        and rule["status"] in ("pending", "later")
    )

    return {
        "should_prompt": should_prompt,
        "rule_id": rule["id"],
        "count": rule["count"],
        "original_phrase": original,
        "modified_phrase": modified,
        "status": rule["status"],
    }


def confirm_rule(rule_id: int):
    """确认规则（句对已在保存时自动存入 Memory Base）"""
    update_rule_status(rule_id, "confirmed")


def ignore_rule(rule_id: int):
    """忽略规则 → 不再提示"""
    update_rule_status(rule_id, "ignored")


def defer_rule(rule_id: int):
    """稍后 → 下次达到阈值再提示"""
    update_rule_status(rule_id, "later")
