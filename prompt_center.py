"""Prompt Center — 管理翻译风格模板 + 领域自动识别"""

import re

# ═══════════════════════════════════════════════════════════════
#  领域自动识别
# ═══════════════════════════════════════════════════════════════

DOMAIN_KEYWORDS = {
    "经济金融": [
        "经济", "金融", "市场", "投资", "股票", "基金", "GDP", "通胀", "利率",
        "贸易", "财政", "货币", "银行", "证券", "汇率", "债券", "期货", "理财",
        "营收", "利润", "资产", "负债", "现金流", "预算", "赤字", "宏观", "微观",
        "economy", "finance", "market", "investment", "stock", "fund", "inflation",
        "interest rate", "trade", "fiscal", "monetary", "bank", "securities",
    ],
    "政治外交": [
        "政治", "外交", "政府", "国家", "总统", "议会", "条约", "协议", "外交官",
        "大使", "主权", "民主", "共和", "政策", "行政", "立法", "国际关系",
        "白宫", "国务院", "联合国", "峰会", "声明", "公报", "制裁",
        "political", "diplomatic", "government", "president", "parliament",
        "treaty", "sovereign", "democracy", "policy", "summit",
    ],
    "法律": [
        "法律", "合同", "法院", "诉讼", "条款", "判决", "律师", "立法", "司法",
        "法规", "依法", "仲裁", "专利", "版权", "侵权", "被告", "原告",
        "legal", "contract", "court", "lawsuit", "clause", "judgment",
        "attorney", "legislation", "arbitration", "patent", "copyright",
    ],
    "传统文化": [
        "传统", "文化", "历史", "哲学", "儒家", "道家", "诗词", "书法", "绘画",
        "节日", "习俗", "孔子", "老子", "诗经", "唐诗", "宋词", "戏曲",
        "traditional", "culture", "philosophy", "Confucian", "poetry",
        "calligraphy", "festival", "custom", "dynasty",
    ],
    "化学化工": [
        "化学", "化工", "分子", "反应", "催化", "合成", "化合物", "元素", "试剂",
        "溶液", "浓度", "催化", "聚合", "氧化", "还原", "电解", "光谱",
        "chemistry", "chemical", "molecule", "reaction", "catalyst",
        "synthesis", "compound", "element", "reagent", "solution",
    ],
    "教育": [
        "教育", "学校", "学生", "教师", "课程", "学习", "考试", "大学", "教学",
        "培训", "学位", "毕业", "论文", "科研", "学术", "奖学金",
        "education", "school", "student", "teacher", "curriculum",
        "exam", "university", "degree", "academic", "research",
    ],
    "医学": [
        "医学", "医疗", "患者", "疾病", "诊断", "治疗", "手术", "药物", "临床",
        "症状", "医生", "护士", "医院", "疫苗", "病毒", "细菌", "药理",
        "medical", "clinical", "patient", "disease", "diagnosis",
        "treatment", "surgery", "drug", "symptom", "hospital",
    ],
}


def detect_domain(text: str) -> str:
    """
    根据文本内容自动识别领域。

    使用关键词匹配：统计各领域命中次数，返回得分最高的领域。
    若无匹配，返回 "其他"。

    Args:
        text: 待分析文本

    Returns:
        领域名称（与 DOMAIN_KEYWORDS 的 key 一致）
    """
    if not text:
        return "其他"

    text_lower = text.lower()
    scores: dict[str, int] = {}

    for domain, keywords in DOMAIN_KEYWORDS.items():
        score = 0
        for kw in keywords:
            # 中文字符直接包含匹配，英文词边界匹配
            if re.search(r"[一-鿿]", kw):
                score += text.count(kw)
            else:
                score += len(re.findall(r"\b" + re.escape(kw.lower()) + r"\b", text_lower))
        if score > 0:
            scores[domain] = score

    if not scores:
        return "其他"

    return max(scores, key=scores.get)


# ═══════════════════════════════════════════════════════════════
#  风格模板配置
# ═══════════════════════════════════════════════════════════════

STYLE_CONFIGS = {
    "default": {
        "style_name": "General Professional",
        "audience": "Professional Readers",
        "description": "通用专业翻译风格，适用于各类文本",
        "principles": [
            "Translate accurately and naturally, preserving the original meaning",
            "Use professional yet accessible language appropriate for the general reader",
            "Maintain consistent terminology throughout",
            "Return only the translation, no explanations",
        ],
        "examples": [],
    },

    "political": {
        "style_name": "Political & Diplomatic",
        "audience": "Professional Readers",
        "description": "政治外交类文本的专业翻译风格",
        "principles": [
            "Use formal, precise, and politically appropriate language",
            "Verify official titles and institutional names against authoritative sources",
            "Maintain diplomatic tone and protocol conventions",
            "Ensure accurate rendering of policy terms and nuanced expressions",
            "Return only the translation, no explanations",
        ],
        "examples": [],
    },

    "financial": {
        "style_name": "Financial & Economic",
        "audience": "Professional Readers",
        "description": "财经类文本的专业翻译风格",
        "principles": [
            "Ensure precise translation of financial terms and instrument names",
            "Correctly format numbers, percentages, currencies, and dates",
            "Maintain professional business tone and register",
            "Preserve market-specific expressions and conventions",
            "Return only the translation, no explanations",
        ],
        "examples": [],
    },

    "legal": {
        "style_name": "Legal",
        "audience": "Professional Readers",
        "description": "法律类文本的专业翻译风格",
        "principles": [
            "Translate with strict accuracy and formal legal language",
            "Maintain consistent use of legal terminology and defined terms",
            "Use shall/may/must with legal precision",
            "Preserve legal structure, clause numbering, and cross-references",
            "Return only the translation, no explanations",
        ],
        "examples": [],
    },

    "custom": {
        "style_name": "Custom",
        "audience": "Professional Readers",
        "description": "用户自定义风格",
        "principles": [
            "Translate accurately and naturally",
            "Return only the translation, no explanations",
        ],
        "examples": [],
    },
}

# 风格标签（UI 展示用）
STYLE_LABELS = {
    "default": "🌐 General Professional",
    "political": "🏛 Political & Diplomatic",
    "financial": "💰 Financial & Economic",
    "legal": "⚖️ Legal",
    "custom": "✏️ Custom",
}

# 兼容旧接口的标签
PROMPT_LABELS = STYLE_LABELS


def get_style_config(template_key: str) -> dict:
    """
    获取指定模板的风格配置。

    Args:
        template_key: default / political / financial / legal / custom

    Returns:
        风格配置字典，包含 style_name, audience, description, principles, examples
    """
    return STYLE_CONFIGS.get(template_key, STYLE_CONFIGS["default"])


def get_prompt(template_key: str, custom_text: str = "") -> str:
    """
    获取指定模板的 System Prompt（向后兼容）。

    用于不需要完整 PromptContext 的场景（如旧代码兼容）。

    Args:
        template_key: default / political / financial / legal / custom
        custom_text: 自定义模板内容（仅 custom 模式使用）

    Returns:
        System Prompt 字符串
    """
    if template_key == "custom":
        return custom_text.strip() or _build_legacy_prompt("default")
    return _build_legacy_prompt(template_key)


def _build_legacy_prompt(template_key: str) -> str:
    """从新配置构建旧版 System Prompt 字符串（向后兼容）"""
    config = STYLE_CONFIGS.get(template_key, STYLE_CONFIGS["default"])

    lines = [
        f"You are a professional translator specializing in {config['style_name'].lower()} translation.",
        "Translate the following text accurately and naturally.",
    ]
    if config.get("principles"):
        lines.append("Pay attention to:")
        for p in config["principles"]:
            if p.startswith("Return only"):
                continue
            lines.append(f"- {p}")
    lines.append("Return only the translation, no explanations.")
    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════
#  提示词覆写支持
# ═══════════════════════════════════════════════════════════════

def get_default_prompt_text(template_key: str) -> str:
    """
    获取指定模板的默认提示词文本（用于 UI 展示）。

    返回 style_name、description、principles 等组成的完整中文提示词。
    """
    config = STYLE_CONFIGS.get(template_key, STYLE_CONFIGS["default"])

    parts = []
    parts.append(f"# {config['style_name']}")
    parts.append(f"描述：{config['description']}")
    parts.append(f"目标受众：{config['audience']}")
    parts.append("")
    parts.append("## 翻译原则")
    for i, p in enumerate(config.get("principles", []), 1):
        parts.append(f"{i}. {p}")

    return "\n".join(parts)


def get_effective_principles(template_key: str) -> list[str]:
    """
    获取指定模板的最终翻译原则（覆写优先）。

    检查数据库中是否有用户覆写，有则解析返回；
    否则返回代码中的默认原则。

    Returns:
        翻译原则列表
    """
    from database import get_prompt_override

    override = get_prompt_override(template_key)
    if override:
        # 将覆写文本按行解析为原则列表
        lines = [line.strip() for line in override.strip().split("\n") if line.strip()]
        # 过滤掉 markdown 标题行
        principles = [l for l in lines if not l.startswith("#")]
        if principles:
            return principles

    # 回退到默认原则
    config = STYLE_CONFIGS.get(template_key, STYLE_CONFIGS["default"])
    return list(config.get("principles", []))
