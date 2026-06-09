"""翻译记忆学习系统 - Streamlit 主应用"""

import io
import csv
import inspect
import streamlit as st
from translator import translate, PROVIDER_LABELS, DIRECTION_LABELS
from config import DEFAULT_PROVIDER
from database import init_db
from tracker import record_modification, confirm_rule, ignore_rule, defer_rule
from document import parse_uploaded_file, filter_chinese_only, filter_english_only
# term_manager 是可选模块：有则使用；没有时启用内置兜底版，避免 Streamlit Cloud 直接 ModuleNotFoundError。
try:
    from term_manager import (
        detect_domains, load_terminology,
        get_terms_for_domain, match_terms,
        build_domain_prompt, DOMAIN_KEYWORDS, DOMAIN_TONES,
    )
except Exception:
    import json
    from pathlib import Path

    DOMAIN_KEYWORDS = {
        "信息技术": [
            "人工智能", "大模型", "大语言模型", "生成式", "算法", "数据", "代码",
            "系统", "模型", "训练", "推理", "检索", "RAG", "LLM", "API", "NLP",
            "prompt", "token", "embedding", "inference", "hallucination",
        ],
        "医疗": ["医疗", "医学", "临床", "患者", "诊断", "治疗", "药物", "疾病", "护理"],
        "法律": ["法律", "合同", "法院", "诉讼", "仲裁", "条款", "责任", "权利", "义务"],
        "金融": ["金融", "银行", "证券", "投资", "资产", "负债", "利率", "汇率", "风险"],
        "传统文化": ["文化", "传统", "非遗", "民俗", "礼仪", "诗词", "书法", "节日"],
        "政治外交": ["外交", "政治", "政府", "政策", "国际", "合作", "治理", "主权"],
        "化学化工": ["化学", "化工", "反应", "催化", "溶液", "材料", "工艺", "分子"],
        "教育": ["教育", "课程", "教学", "学习", "学生", "教师", "考试", "培养"],
        "其他": [],
    }

    DOMAIN_TONES = {
        "信息技术": "简练、现代、注重逻辑，符合科技产品 UI、技术文档及开发者阅读习惯",
        "医疗": "准确、谨慎、专业，避免夸大疗效或改变医学含义",
        "法律": "严谨、正式、保守，保持法律概念边界清晰",
        "金融": "准确、客观、风险意识明确，符合财经文本表达",
        "传统文化": "典雅、自然，保留文化意象并兼顾可理解性",
        "政治外交": "正式、稳健、中性，避免口语化和过度解释",
        "化学化工": "技术准确、单位和工艺表达规范",
        "教育": "清晰、平实、符合教学与学术表达习惯",
        "其他": "自然、准确、清晰",
    }

    def _contains_chinese(value: str) -> bool:
        return any("\u4e00" <= ch <= "\u9fff" for ch in str(value or ""))

    def _dedupe_term_pairs(terms):
        seen = set()
        out = []
        for a, b in terms or []:
            a = str(a or "").strip()
            b = str(b or "").strip()
            if not a or not b:
                continue
            key = (a.lower(), b.lower())
            if key in seen:
                continue
            seen.add(key)
            out.append((a, b))
        return out

    def detect_domains(text: str, domain_keywords: dict | None = None) -> list[tuple[str, int]]:
        text_lower = str(text or "").lower()
        keywords = domain_keywords or DOMAIN_KEYWORDS
        scores = []
        for domain, words in keywords.items():
            score = 0
            for word in words:
                w = str(word or "")
                if not w:
                    continue
                if _contains_chinese(w):
                    score += str(text or "").count(w)
                else:
                    score += text_lower.count(w.lower())
            if score > 0:
                scores.append((domain, score))
        return sorted(scores, key=lambda x: x[1], reverse=True)

    def load_terminology() -> dict[str, list[tuple[str, str]]]:
        """兜底读取术语：优先 terminology.csv；没有则读取 terminology.json。"""
        base_dir = Path(__file__).resolve().parent
        result: dict[str, list[tuple[str, str]]] = {}

        csv_candidates = [
            base_dir / "terminology.csv",
            base_dir / "term_tool" / "terminology.csv",
            base_dir / "data" / "terminology.csv",
        ]
        for path in csv_candidates:
            if not path.exists():
                continue
            try:
                import csv
                with open(path, "r", encoding="utf-8-sig", newline="") as f:
                    rows = list(csv.reader(f))
                if not rows:
                    continue
                header = [h.strip().lower() for h in rows[0]]
                data_rows = rows[1:] if any(h in header for h in ["中文", "英文", "domain", "领域"]) else rows

                def find_idx(names, default):
                    for name in names:
                        if name in header:
                            return header.index(name)
                    return default

                zh_idx = find_idx(["中文", "chinese", "zh", "source", "source_text"], 0)
                en_idx = find_idx(["英文", "english", "en", "target", "target_text"], 1)
                dom_idx = None
                for name in ["领域", "domain", "field", "category"]:
                    if name in header:
                        dom_idx = header.index(name)
                        break

                for row in data_rows:
                    if len(row) <= max(zh_idx, en_idx):
                        continue
                    zh = row[zh_idx].strip()
                    en = row[en_idx].strip()
                    dom = row[dom_idx].strip() if dom_idx is not None and dom_idx < len(row) and row[dom_idx].strip() else "其他"
                    if zh and en:
                        result.setdefault(dom, []).append((zh, en))
            except Exception:
                continue

        json_path = base_dir / "terminology.json"
        if json_path.exists():
            try:
                with open(json_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    for src, tgt in data.items():
                        src = str(src or "").strip()
                        tgt = str(tgt or "").strip()
                        if not src or not tgt:
                            continue
                        # terminology.json 原项目多为 英文 → 中文；app 内部统一为 中文 → English
                        if _contains_chinese(src) and not _contains_chinese(tgt):
                            pair = (src, tgt)
                        elif _contains_chinese(tgt) and not _contains_chinese(src):
                            pair = (tgt, src)
                        else:
                            pair = (src, tgt)
                        result.setdefault("信息技术", []).append(pair)
            except Exception:
                pass

        return {d: _dedupe_term_pairs(ts) for d, ts in result.items()}

    def get_terms_for_domain(domain: str, terminology: dict) -> list[tuple[str, str]]:
        return list((terminology or {}).get(domain, []))

    def match_terms(text: str, terms: list[tuple[str, str]]):
        text = str(text or "")
        text_lower = text.lower()
        matched = []
        positions = []
        for zh, en in sorted(_dedupe_term_pairs(terms), key=lambda p: max(len(p[0]), len(p[1])), reverse=True):
            hit_pos = None
            hit_len = 0
            for cand in (zh, en):
                if not cand:
                    continue
                if _contains_chinese(cand):
                    pos = text.find(cand)
                else:
                    pos = text_lower.find(cand.lower())
                if pos >= 0:
                    hit_pos = pos
                    hit_len = len(cand)
                    break
            if hit_pos is not None:
                matched.append((zh, en))
                positions.append((hit_pos, hit_pos + hit_len, zh, en))
        return matched, positions

    def build_domain_prompt(text: str, domain: str | None = None, matched_terms: list[tuple[str, str]] | None = None) -> str:
        actual_domain = domain or "其他"
        tone = DOMAIN_TONES.get(actual_domain, DOMAIN_TONES["其他"])
        lines = [
            "# Role",
            f"你是一位资深的{actual_domain}领域翻译专家。",
            "",
            "# Style",
            tone,
            "",
            "# Core Requirements",
            "- 忠实传达原文含义，不增译、不漏译关键信息。",
            "- 保持术语、数字、专名、缩写和单位一致。",
            "- 输出译文即可，除非用户另有要求，不要解释。",
        ]
        if matched_terms:
            lines.extend([
                "",
                "# Terminology & Rules",
                "以下术语来自当前术语库，并且已在待翻译文本中命中；翻译时必须优先采用：",
            ])
            for zh, en in matched_terms:
                lines.append(f"- {zh} → {en}")
        return "\n".join(lines)


st.set_page_config(page_title="翻译记忆学习系统", page_icon="🌐", layout="wide")
init_db()


# ═══════════════════════════════════════════════════════
# 术语注入辅助函数
# 目的：把 Tab3 数据库术语 + term_manager 静态术语统一用于 Tab1 翻译
# ═══════════════════════════════════════════════════════
DOMAIN_ALIASES = {
    "医学": "医疗",
    "经济金融": "金融",
}
DOC_DOMAIN_OPTIONS = [
    "自动检测", "信息技术", "医疗", "法律", "金融", "经济金融",
    "传统文化", "政治外交", "化学化工", "教育", "医学", "其他",
]
MAX_PROMPT_TERMS = 40


def normalize_domain(domain: str | None) -> str:
    """统一页面领域名和术语库领域名，例如：医学→医疗，经济金融→金融。"""
    d = (domain or "其他").strip() or "其他"
    return DOMAIN_ALIASES.get(d, d)


def dedupe_terms(terms: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """术语去重，保留首次出现顺序。"""
    seen: set[tuple[str, str]] = set()
    result: list[tuple[str, str]] = []
    for ch, en in terms:
        ch = (ch or "").strip()
        en = (en or "").strip()
        if not ch or not en:
            continue
        key = (ch.lower(), en.lower())
        if key in seen:
            continue
        seen.add(key)
        result.append((ch, en))
    return result


def load_db_terminology() -> dict[str, list[tuple[str, str]]]:
    """读取 Tab3 页面导入/保存到数据库中的术语。"""
    try:
        from database import get_all_assets
        try:
            assets = get_all_assets(domain=None, keyword=None)
        except TypeError:
            assets = get_all_assets()
    except Exception:
        return {}

    terminology: dict[str, list[tuple[str, str]]] = {}
    for a in assets or []:
        # get_all_assets 通常返回 sqlite Row / dict，二者都兼容
        try:
            source = (a.get("source_text") or "").strip()
            target = (a.get("target_text") or "").strip()
            domain = normalize_domain(a.get("domain") or "其他")
        except AttributeError:
            source = (a["source_text"] or "").strip()
            target = (a["target_text"] or "").strip()
            domain = normalize_domain(a["domain"] or "其他")

        if not source or not target:
            continue
        terminology.setdefault(domain, []).append((source, target))
    return {d: dedupe_terms(ts) for d, ts in terminology.items()}


def merge_terminology_dicts(*items: dict[str, list[tuple[str, str]]]) -> dict[str, list[tuple[str, str]]]:
    """合并静态 CSV 术语和数据库术语。"""
    merged: dict[str, list[tuple[str, str]]] = {}
    for terminology in items:
        for domain, terms in (terminology or {}).items():
            nd = normalize_domain(domain)
            merged.setdefault(nd, []).extend(terms or [])
    return {d: dedupe_terms(ts) for d, ts in merged.items()}


def load_app_terminology() -> dict[str, list[tuple[str, str]]]:
    """
    页面实际使用的术语库：
    1. term_manager.load_terminology() 读取的静态 CSV；
    2. Tab3 导入/人工保存后进入数据库的动态术语。
    """
    try:
        csv_terms = load_terminology() or {}
    except Exception:
        csv_terms = {}
    db_terms = load_db_terminology()
    return merge_terminology_dicts(csv_terms, db_terms)


def flatten_terminology(terminology: dict[str, list[tuple[str, str]]]) -> list[tuple[str, str]]:
    terms: list[tuple[str, str]] = []
    for values in terminology.values():
        terms.extend(values or [])
    return dedupe_terms(terms)


def build_term_context(text: str, selected_domain: str | None) -> dict:
    """对单句/段落做领域检测、术语筛选、术语匹配，并生成真正要传给模型的 prompt。"""
    terminology = load_app_terminology()
    detected = detect_domains(text, DOMAIN_KEYWORDS)

    selected = normalize_domain(selected_domain)
    detected_domains = [normalize_domain(d) for d, _ in detected]

    if selected_domain and selected_domain != "自动检测" and selected != "其他":
        best_domain = selected
    elif detected_domains:
        best_domain = detected_domains[0]
    else:
        best_domain = "其他"

    active_domains: list[str] = []
    if best_domain != "其他":
        active_domains.append(best_domain)
    active_domains.extend(detected_domains)
    active_domains.append("其他")

    # 去重并只保留术语库中存在的领域；如果完全检测不到，就退回全库匹配
    active_domains = list(dict.fromkeys(active_domains))
    active_terms: list[tuple[str, str]] = []
    for d in active_domains:
        active_terms.extend(get_terms_for_domain(d, terminology))
    active_terms = dedupe_terms(active_terms)

    if not active_terms:
        active_terms = flatten_terminology(terminology)

    matched_terms: list[tuple[str, str]] = []
    match_positions = []
    if active_terms:
        matched_terms, match_positions = match_terms(text, active_terms)
        matched_terms = dedupe_terms(matched_terms)

    # 如果用户选了某个领域但没有命中，尝试全库兜底，避免术语领域写错时完全失效
    if not matched_terms:
        all_terms = flatten_terminology(terminology)
        if all_terms:
            matched_terms, match_positions = match_terms(text, all_terms)
            matched_terms = dedupe_terms(matched_terms)

    matched_terms = matched_terms[:MAX_PROMPT_TERMS]
    system_prompt = build_domain_prompt(
        text=text,
        domain=best_domain if best_domain != "其他" else None,
        matched_terms=matched_terms if matched_terms else None,
    )

    return {
        "domain": best_domain,
        "selected_domain": selected_domain or "自动检测",
        "detected": detected,
        "active_domains": active_domains,
        "matched_terms": matched_terms,
        "match_positions": match_positions,
        "system_prompt": system_prompt,
        "term_total": sum(len(v) for v in terminology.values()),
    }




def resolve_asset_domain_for_save(source_text: str) -> str:
    """保存人工修订到术语/记忆库时，避免把领域写成“自动检测”。"""
    selected = st.session_state.get("app_domain", "自动检测")
    if selected and selected != "自动检测":
        return normalize_domain(selected)
    detected = detect_domains(source_text or "", DOMAIN_KEYWORDS)
    if detected:
        return normalize_domain(detected[0][0])
    return "其他"

def translate_with_term_context(text: str, *, provider: str, direction: str, selected_domain: str):
    """
    包装原 translate()：
    - 先匹配术语并生成 system_prompt；
    - 如果 translator.translate 支持 system_prompt/matched_terms/domain 参数，就传入；
    - 如果暂不支持，则退回原调用，并在 retrieval 中标记 translator_needs_patch=True。
    """
    term_context = build_term_context(text, selected_domain)

    base_kwargs = {"provider": provider, "direction": direction}
    context_kwargs = {
        "domain": term_context["domain"],
        "matched_terms": term_context["matched_terms"],
        "terminology": term_context["matched_terms"],
        "system_prompt": term_context["system_prompt"],
        "term_prompt": term_context["system_prompt"],
    }

    try:
        sig = inspect.signature(translate)
        params = sig.parameters
        accepts_kwargs = any(p.kind == inspect.Parameter.VAR_KEYWORD for p in params.values())
        supported_context_kwargs = {
            k: v for k, v in context_kwargs.items()
            if accepts_kwargs or k in params
        }
    except Exception:
        supported_context_kwargs = {}

    used_term_prompt = bool(supported_context_kwargs)
    try:
        translation, retrieval = translate(text, **base_kwargs, **supported_context_kwargs)
    except TypeError:
        # 兼容旧版 translator.translate(text, provider, direction)
        used_term_prompt = False
        translation, retrieval = translate(text, **base_kwargs)

    retrieval = retrieval or {}
    retrieval.setdefault("domain", term_context["domain"])
    retrieval.setdefault("hits", [])
    retrieval.setdefault("count", len(retrieval.get("hits", [])))
    retrieval["matched_terms"] = term_context["matched_terms"]
    retrieval["term_prompt"] = term_context["system_prompt"]
    retrieval["term_total"] = term_context["term_total"]
    retrieval["used_term_prompt"] = used_term_prompt
    retrieval["translator_needs_patch"] = not used_term_prompt
    return translation, retrieval


# ── Session State ────────────────────────────────────
if "style_prompt" not in st.session_state:
    st.session_state.style_prompt = None
if "provider" not in st.session_state:
    st.session_state.provider = DEFAULT_PROVIDER
if "segments" not in st.session_state:
    st.session_state.segments = []
if "doc_translations" not in st.session_state:
    st.session_state.doc_translations = {}
if "doc_direction" not in st.session_state:
    st.session_state.doc_direction = "zh2en"
if "doc_edits" not in st.session_state:
    st.session_state.doc_edits = {}
if "doc_editing" not in st.session_state:
    st.session_state.doc_editing = False
if "app_domain" not in st.session_state:
    st.session_state.app_domain = "自动检测"
if "last_retrieval" not in st.session_state:
    st.session_state.last_retrieval = None
if "last_term_context" not in st.session_state:
    st.session_state.last_term_context = None

# ── 侧边栏 ────────────────────────────────────────────
with st.sidebar:
    st.header("⚙️ 模型")
    provider = st.selectbox(
        "翻译引擎",
        options=list(PROVIDER_LABELS.keys()),
        format_func=lambda k: PROVIDER_LABELS[k],
        key="provider_selector",
    )
    st.session_state.provider = provider

# ── 主界面 ────────────────────────────────────────────
st.title("🌐 翻译记忆学习系统")

tab1, tab2, tab3, tab4 = st.tabs(["📄 文档翻译", "📁 我的文件", "📚 术语库", "🔬 领域术语"])

# ═══════════════════════════════════════════════════════
#  Tab 1：文档翻译
# ═══════════════════════════════════════════════════════
with tab1:
    uploaded_file = st.file_uploader(
        "上传文档", type=["docx", "txt"],
        help="支持 .docx 和 .txt 格式", key="doc_uploader",
    )

    if uploaded_file is not None:
        if st.button("🔍 解析文档", type="primary"):
            with st.spinner("正在解析文档..."):
                try:
                    st.session_state.segments = parse_uploaded_file(uploaded_file)
                    from database import add_file_history
                    seg_count = len(st.session_state.segments)
                    add_file_history(uploaded_file.name, seg_count)
                    if seg_count == 0:
                        file_size = getattr(uploaded_file, "size", None)
                        size_hint = f"，文件大小约 {file_size} bytes" if file_size is not None else ""
                        st.warning(
                            f"解析完成，但没有提取到可翻译文本（0 个句子{size_hint}）。"
                            "常见原因：文档内容是扫描图片/OCR 图片、Word 中为特殊文本框/嵌入对象、"
                            "或原解析器未覆盖表格结构。请确认已更新 document.py；若仍为 0，建议先另存为 .docx 或 .txt 后重试。"
                        )
                    else:
                        st.success(f"解析完成！共 {seg_count} 个句子")
                except Exception as e:
                    st.error(f"解析失败：{e}")
                    st.session_state.segments = []

    if st.session_state.segments:
        lang_filter = st.radio(
            "📋 句子过滤", options=["全部显示", "仅显示中文", "仅显示英文"],
            horizontal=True, key="lang_filter",
        )
        if lang_filter == "仅显示中文":
            display_segments = filter_chinese_only(st.session_state.segments)
            col_label = "Source Text（中文）"
        elif lang_filter == "仅显示英文":
            display_segments = filter_english_only(st.session_state.segments)
            col_label = "Source Text（英文）"
        else:
            display_segments = st.session_state.segments
            col_label = "Source Text"

        if display_segments:
            st.markdown(f"**共 {len(display_segments)} 个句子**（全部 {len(st.session_state.segments)} 个）")

            col_dir, col_dom, col_btn = st.columns([1, 1, 2])
            with col_dir:
                st.session_state.doc_direction = st.selectbox(
                    "翻译方向", options=list(DIRECTION_LABELS.keys()),
                    format_func=lambda k: DIRECTION_LABELS[k], key="doc_direction_selector",
                )
            with col_dom:
                st.session_state.app_domain = st.selectbox(
                    "Domain / 术语领域",
                    options=DOC_DOMAIN_OPTIONS,
                    key="doc_domain",
                    help="建议选择「自动检测」；如果自动识别不准，可手动指定领域。",
                )
            with col_btn:
                st.write(""); st.write("")
                translate_all_clicked = st.button("🌐 翻译全部", type="primary", use_container_width=True)

            if translate_all_clicked:
                st.session_state.doc_translations = {}
                st.session_state.last_retrieval = None
                st.session_state.last_term_context = None
                progress_bar = st.progress(0)
                total = len(display_segments)
                term_used_count = 0
                term_matched_total = 0
                needs_translator_patch = False

                for i, seg in enumerate(display_segments):
                    try:
                        translation, retrieval = translate_with_term_context(
                            seg["source_text"],
                            provider=st.session_state.provider,
                            direction=st.session_state.doc_direction,
                            selected_domain=st.session_state.app_domain,
                        )
                        st.session_state.doc_translations[seg["sentence_id"]] = translation
                        if retrieval:
                            st.session_state.last_retrieval = retrieval
                            st.session_state.last_term_context = retrieval
                            term_matched_total += len(retrieval.get("matched_terms", []))
                            if retrieval.get("used_term_prompt"):
                                term_used_count += 1
                            if retrieval.get("translator_needs_patch"):
                                needs_translator_patch = True
                    except Exception as e:
                        st.session_state.doc_translations[seg["sentence_id"]] = f"❌ {e}"
                    progress_bar.progress((i + 1) / total)

                if needs_translator_patch:
                    st.warning(
                        "已完成术语匹配，但当前 translator.translate() 不接收 system_prompt/matched_terms 参数，"
                        "术语可能没有真正进入模型。请同步修改 translator.py 的 translate() 函数签名和 Prompt 拼接逻辑。"
                    )
                else:
                    st.success(f"翻译完成！共 {total} 句；术语 Prompt 已注入 {term_used_count} 句，累计匹配 {term_matched_total} 个术语。")

            # ── 检索结果 / 术语注入展示 ────────────────────────────
            if st.session_state.get("last_retrieval"):
                r = st.session_state.last_retrieval
                domain = r.get("domain", "未知")
                matched_terms = r.get("matched_terms", [])
                used_label = "已注入模型" if r.get("used_term_prompt") else "仅匹配，未注入"
                with st.expander(
                    f"🔍 本次翻译使用 · 模型：{PROVIDER_LABELS[st.session_state.provider]} · "
                    f"领域：{domain} · "
                    f"术语匹配：{len(matched_terms)} 个 · {used_label}",
                    expanded=False,
                ):
                    st.caption(f"当前可用术语总量：{r.get('term_total', 0)}")
                    if r.get("translator_needs_patch"):
                        st.warning(
                            "translator.translate() 目前没有接收 system_prompt/matched_terms，"
                            "因此页面能匹配术语，但模型未必按术语表翻译。"
                        )
                    if matched_terms:
                        st.markdown("**📋 匹配术语**")
                        for ch, en in matched_terms:
                            st.caption(f"{ch} → {en}")
                    if r.get("term_prompt"):
                        with st.expander("查看本句传入模型的术语 Prompt", expanded=False):
                            st.code(r["term_prompt"], language="markdown")
                    if r.get("hits"):
                        st.markdown("**📖 TM 命中**")
                        for h in r["hits"]:
                            st.caption(f"{h['source_text']} → {h['target_text']}")

            # 修改模式切换
            if st.session_state.doc_translations:
                col_tbl, col_edit, _ = st.columns([2, 1, 1])
                with col_edit:
                    if not st.session_state.doc_editing:
                        if st.button("✏️ 批量修改", use_container_width=True):
                            st.session_state.doc_editing = True
                            for seg in display_segments:
                                sid = seg["sentence_id"]
                                if sid not in st.session_state.doc_edits:
                                    st.session_state.doc_edits[sid] = st.session_state.doc_translations.get(sid, "")
                            st.rerun()
                    else:
                        if st.button("👁 查看模式", use_container_width=True):
                            st.session_state.doc_editing = False
                            st.rerun()

            # 编辑模式
            if st.session_state.doc_editing and st.session_state.doc_translations:
                st.info("📝 横向对比修改每条译文，可逐句保存或一键保存全部")
                for seg in display_segments:
                    sid = seg["sentence_id"]
                    ai_text = st.session_state.doc_translations.get(sid, "")
                    saved_mark = " ✅" if f"saved_{sid}" in st.session_state and st.session_state[f"saved_{sid}"] else ""
                    with st.expander(f"{sid}{saved_mark} — {seg['source_text'][:80]}...", expanded=False):
                        col_src, col_ai, col_edit = st.columns([1, 1, 1])
                        with col_src:
                            st.markdown("**原文**")
                            st.markdown(f'<div style="background:#f0f2f6;padding:12px;border-radius:8px;min-height:120px;white-space:pre-wrap;font-size:14px;">{seg["source_text"]}</div>', unsafe_allow_html=True)
                        with col_ai:
                            st.markdown("**AI Translation**")
                            st.markdown(f'<div style="background:#e8f5e9;padding:12px;border-radius:8px;min-height:120px;white-space:pre-wrap;font-size:14px;word-wrap:break-word;">{ai_text}</div>', unsafe_allow_html=True)
                        with col_edit:
                            st.markdown("**Human Translation**")
                            edited = st.text_area("Human Translation", value=st.session_state.doc_edits.get(sid, ai_text), height=140, label_visibility="collapsed", key=f"edit_{sid}")
                            st.session_state.doc_edits[sid] = edited
                            if st.button(f"💾 保存 {sid}", key=f"save_{sid}", use_container_width=True):
                                original, modified = ai_text, edited
                                if original.strip() != modified.strip():
                                    result = record_modification(source=seg["source_text"], original=original, modified=modified)
                                    if result["should_prompt"]:
                                        st.session_state.style_prompt = {"rule_id": result["rule_id"], "original": result["original_phrase"], "modified": result["modified_phrase"], "count": result["count"], "status": result["status"]}
                                    from database import insert_asset
                                    insert_asset(source=seg["source_text"], target=modified, domain=resolve_asset_domain_for_save(seg["source_text"]))
                                st.session_state[f"saved_{sid}"] = True
                                st.success(f"{sid} 已保存 → 术语库")
                                st.rerun()

                col_save_all, _ = st.columns([1, 3])
                with col_save_all:
                    if st.button("💾 保存全部修改", type="primary", use_container_width=True):
                        saved_count, last_prompt = 0, None
                        from database import insert_asset
                        for seg in display_segments:
                            sid = seg["sentence_id"]
                            original = st.session_state.doc_translations.get(sid, "")
                            modified = st.session_state.doc_edits.get(sid, "")
                            if original.strip() != modified.strip():
                                result = record_modification(source=seg["source_text"], original=original, modified=modified)
                                if result["should_prompt"]:
                                    last_prompt = {"rule_id": result["rule_id"], "original": result["original_phrase"], "modified": result["modified_phrase"], "count": result["count"], "status": result["status"]}
                                insert_asset(source=seg["source_text"], target=modified, domain=resolve_asset_domain_for_save(seg["source_text"]))
                            st.session_state[f"saved_{sid}"] = True
                            saved_count += 1
                        st.session_state.doc_editing = False
                        if last_prompt:
                            st.session_state.style_prompt = last_prompt
                        st.success(f"已保存 {saved_count} 条 → 术语库")
                        st.rerun()

            # 表格
            if st.session_state.doc_translations:
                if st.session_state.doc_edits:
                    headers = ["Sentence ID", "Source Text", "AI Translation", "Human Translation"]
                    rows_html = "".join(
                        f"<tr><td style='white-space:nowrap;vertical-align:top;padding:8px;'>{seg['sentence_id']}</td>"
                        f"<td style='white-space:pre-wrap;word-wrap:break-word;vertical-align:top;padding:8px;'>{seg['source_text']}</td>"
                        f"<td style='white-space:pre-wrap;word-wrap:break-word;vertical-align:top;padding:8px;'>{st.session_state.doc_translations.get(seg['sentence_id'], '')}</td>"
                        f"<td style='white-space:pre-wrap;word-wrap:break-word;vertical-align:top;padding:8px;'>{st.session_state.doc_edits.get(seg['sentence_id'], '')}</td></tr>"
                        for seg in display_segments
                    )
                else:
                    headers = ["Sentence ID", "Source Text", "AI Translation"]
                    rows_html = "".join(
                        f"<tr><td style='white-space:nowrap;vertical-align:top;padding:8px;'>{seg['sentence_id']}</td>"
                        f"<td style='white-space:pre-wrap;word-wrap:break-word;vertical-align:top;padding:8px;'>{seg['source_text']}</td>"
                        f"<td style='white-space:pre-wrap;word-wrap:break-word;vertical-align:top;padding:8px;'>{st.session_state.doc_translations.get(seg['sentence_id'], '')}</td></tr>"
                        for seg in display_segments
                    )
            else:
                headers = ["Sentence ID", "Source Text"]
                rows_html = "".join(
                    f"<tr><td style='white-space:nowrap;vertical-align:top;padding:8px;'>{seg['sentence_id']}</td>"
                    f"<td style='white-space:pre-wrap;word-wrap:break-word;vertical-align:top;padding:8px;'>{seg['source_text']}</td></tr>"
                    for seg in display_segments
                )

            header_html = "".join(f"<th style='position:sticky;top:0;background:#e0e0e0;padding:10px;text-align:left;'>{h}</th>" for h in headers)
            st.markdown(f"<div style='max-height:500px;overflow-y:auto;border:1px solid #ddd;border-radius:8px;'><table style='width:100%;border-collapse:collapse;font-size:14px;'><thead><tr>{header_html}</tr></thead><tbody>{rows_html}</tbody></table></div>", unsafe_allow_html=True)
            st.caption(f"共 {len(display_segments)} 条")

            # CSV 导出
            csv_buffer = io.StringIO(newline="")
            writer = csv.writer(csv_buffer, quoting=csv.QUOTE_ALL)
            if st.session_state.doc_translations:
                if st.session_state.doc_edits:
                    writer.writerow(["Sentence ID", "Source Text", "AI Translation", "Human Translation"])
                    for seg in display_segments:
                        sid = seg["sentence_id"]
                        writer.writerow([sid, seg["source_text"], st.session_state.doc_translations.get(sid, ""), st.session_state.doc_edits.get(sid, "")])
                else:
                    writer.writerow(["Sentence ID", "Source Text", "AI Translation"])
                    for seg in display_segments:
                        writer.writerow([seg["sentence_id"], seg["source_text"], st.session_state.doc_translations.get(seg["sentence_id"], "")])
            else:
                writer.writerow(["Sentence ID", "Source Text"])
                for seg in display_segments:
                    writer.writerow([seg["sentence_id"], seg["source_text"]])
            csv_bytes = csv_buffer.getvalue().encode("utf-8-sig")
            import base64
            st.markdown(f'<a href="data:text/csv;charset=utf-8;base64,{base64.b64encode(csv_bytes).decode()}" download="sentences.csv" style="display:inline-block;padding:6px 16px;background:#4CAF50;color:#fff;text-decoration:none;border-radius:6px;font-size:14px;">📥 下载 CSV</a>', unsafe_allow_html=True)
        else:
            st.warning("过滤后无匹配句子")
    else:
        st.info("👆 请上传 .docx 或 .txt 文件，然后点击「解析文档」")

# ═══════════════════════════════════════════════════════
#  Tab 2：我的文件
# ═══════════════════════════════════════════════════════
with tab2:
    st.subheader("📁 我的文件")
    from database import get_file_history, delete_file_history

    files = get_file_history(limit=50)
    if files:
        for f in files:
            col_f, col_d = st.columns([6, 1])
            with col_f:
                st.markdown(f"📄 **{f['filename']}** — {f['sentence_count']} 句 — _{f['uploaded_at']}_")
            with col_d:
                if st.button("🗑", key=f"tab2_del_{f['id']}"):
                    delete_file_history(f["id"])
                    st.rerun()
    else:
        st.info("暂无上传记录")

# ═══════════════════════════════════════════════════════
#  Tab 3：术语库
# ═══════════════════════════════════════════════════════
with tab3:
    from database import get_all_assets, get_asset_stats, get_asset_domains, delete_asset, insert_asset

    DOMAIN_OPTIONS = ["医疗", "法律", "信息技术", "金融", "经济金融", "传统文化", "政治外交", "化学化工", "教育", "其他"]

    def auto_detect_columns(headers: list[str], sample_rows: list[list[str]]) -> dict:
        """
        自动识别 CSV 列的中文/英文/领域归属。
        返回 {'zh_col': str, 'en_col': str, 'domain_col': str|None}
        """
        import re

        zh_pattern = re.compile(r"[一-鿿]")
        en_pattern = re.compile(r"^[a-zA-Z0-9\s\-_.,;:!?()/&+\"'<>\[\]{}|~@#$%^*=]+$")

        col_data = {h: [] for h in headers}
        for row in sample_rows:
            for i, h in enumerate(headers):
                if i < len(row):
                    col_data[h].append(str(row[i]) if row[i] is not None else "")

        scores = {}
        for h, vals in col_data.items():
            non_empty = [v for v in vals if v.strip()]
            if not non_empty:
                scores[h] = {"zh": 0, "en": 0, "domain_hint": 0}
                continue
            zh_count = sum(1 for v in non_empty if zh_pattern.search(v))
            en_count = sum(1 for v in non_empty if en_pattern.match(v.strip()))
            zh_ratio = zh_count / len(non_empty)
            en_ratio = en_count / len(non_empty)
            hl = h.lower()
            domain_hint = 1 if any(kw in hl for kw in ["领域", "domain", "field", "category", "行业", "分类", "类型"]) else 0
            scores[h] = {"zh": zh_ratio, "en": en_ratio, "domain_hint": domain_hint}

        zh_candidates = sorted(
            [(h, s) for h, s in scores.items() if s["zh"] >= 0.3],
            key=lambda x: x[1]["zh"], reverse=True,
        )
        zh_col = zh_candidates[0][0] if zh_candidates else headers[0] if headers else ""

        en_candidates = sorted(
            [(h, s) for h, s in scores.items() if s["en"] >= 0.3 and h != zh_col],
            key=lambda x: x[1]["en"], reverse=True,
        )
        en_col = en_candidates[0][0] if en_candidates else (headers[1] if len(headers) > 1 else "")

        domain_col = None
        domain_candidates = sorted(
            [(h, s) for h, s in scores.items() if h != zh_col and h != en_col],
            key=lambda x: (-x[1]["domain_hint"], x[1]["zh"] + x[1]["en"]),
        )
        if domain_candidates:
            best = domain_candidates[0]
            domain_col = best[0]

        return {"zh_col": zh_col, "en_col": en_col, "domain_col": domain_col}

    # ── 统计 ──
    stats = get_asset_stats()
    m1, m2, m3 = st.columns(3)
    with m1: st.metric("📊 术语总数", stats["total"])
    with m2: st.metric("✅ 启用", stats.get("active", stats["total"]))
    with m3: st.metric("📂 领域数", len(get_asset_domains()) if stats["total"] > 0 else 0)

    # ── 上传 CSV ──
    with st.expander("📥 上传术语 CSV", expanded=False):
        st.caption("CSV 文件将自动识别中文列、英文列和领域列")
        imp_file = st.file_uploader(
            "选择 CSV 文件", type=["csv"], key="term_import_file",
            label_visibility="collapsed",
        )
        if imp_file is not None:
            raw_bytes = imp_file.read()
            imp_file.seek(0)
            try:
                content = raw_bytes.decode("utf-8-sig")
            except UnicodeDecodeError:
                content = raw_bytes.decode("gbk", errors="replace")

            reader = csv.reader(io.StringIO(content))
            rows_list = list(reader)
            if len(rows_list) < 2:
                st.warning("CSV 至少需要表头 + 1 行数据")
            else:
                headers = rows_list[0]
                sample_rows = rows_list[1:6]
                all_rows = rows_list[1:]

                detected = auto_detect_columns(headers, sample_rows)

                col_a, col_b, col_c = st.columns(3)
                with col_a:
                    st.success(f"🇨🇳 中文列 → **{detected['zh_col']}**")
                with col_b:
                    st.success(f"🇬🇧 英文列 → **{detected['en_col']}**")
                with col_c:
                    if detected["domain_col"]:
                        st.info(f"🏷 领域列 → **{detected['domain_col']}**")
                    else:
                        st.caption("未检测到领域列，默认使用「其他」")

                st.markdown("**📋 预览（前 5 条）**")
                zh_idx = headers.index(detected["zh_col"]) if detected["zh_col"] in headers else 0
                en_idx = headers.index(detected["en_col"]) if detected["en_col"] in headers else (1 if len(headers) > 1 else 0)
                domain_idx = headers.index(detected["domain_col"]) if detected.get("domain_col") and detected["domain_col"] in headers else None

                preview_rows = []
                for row in all_rows[:5]:
                    zh_val = row[zh_idx].strip() if zh_idx < len(row) else ""
                    en_val = row[en_idx].strip() if en_idx < len(row) else ""
                    domain_val = row[domain_idx].strip() if domain_idx is not None and domain_idx < len(row) else "其他"
                    preview_rows.append((zh_val, en_val, domain_val))

                preview_html = (
                    "<table style='width:100%;border-collapse:collapse;font-size:14px;'>"
                    "<tr style='background:#e0e0e0;'><th style='padding:8px;text-align:left;'>中文</th><th style='padding:8px;text-align:left;'>英文</th><th style='padding:8px;text-align:left;'>领域</th></tr>"
                    + "".join(
                        f"<tr><td style='padding:8px;border-bottom:1px solid #eee;'>{zh}</td><td style='padding:8px;border-bottom:1px solid #eee;'>{en}</td><td style='padding:8px;border-bottom:1px solid #eee;'>{dom}</td></tr>"
                        for zh, en, dom in preview_rows
                    )
                    + "</table>"
                )
                st.markdown(preview_html, unsafe_allow_html=True)

                if st.button("📥 确认导入", type="primary", use_container_width=True, key="term_confirm_import"):
                    imported = skipped = 0
                    for row in all_rows:
                        zh_val = (row[zh_idx].strip() if zh_idx < len(row) else "")
                        en_val = (row[en_idx].strip() if en_idx < len(row) else "")
                        domain_val = (row[domain_idx].strip() if domain_idx is not None and domain_idx < len(row) else "其他")
                        if not zh_val or not en_val:
                            skipped += 1
                            continue
                        insert_asset(source=zh_val, target=en_val, domain=domain_val if domain_val else "其他")
                        imported += 1

                    msg = f"✅ 导入 {imported} 条术语"
                    if skipped:
                        msg += f"，跳过 {skipped} 条空行"
                    st.success(msg)
                    st.rerun()

    # ── 搜索 + 领域筛选 ──
    st.divider()

    existing_domains = get_asset_domains()
    domain_options = ["全部"] + [d for d in DOMAIN_OPTIONS if d in existing_domains] + [d for d in existing_domains if d not in DOMAIN_OPTIONS]

    c1, c2 = st.columns(2)
    with c1:
        keyword = st.text_input("🔍 搜索术语", key="term_search", placeholder="输入中文或英文关键词...")
    with c2:
        domain_filter = st.selectbox("🏷 领域筛选", options=domain_options, key="term_domain")

    assets = get_all_assets(
        domain=None if domain_filter == "全部" else domain_filter,
        keyword=keyword.strip() or None,
    )

    st.markdown(f"**共 {len(assets)} 条术语**" + (f"（总计 {stats['total']} 条）" if keyword or domain_filter != "全部" else ""))

    # ── 术语表格 ──
    if not assets:
        st.info("🙅 暂无术语。请上传 CSV 文件导入术语库。")
    else:
        table_html = (
            "<div style='max-height:550px;overflow-y:auto;border:1px solid #ddd;border-radius:8px;'>"
            "<table style='width:100%;border-collapse:collapse;font-size:14px;'>"
            "<thead><tr style='position:sticky;top:0;background:#e0e0e0;'>"
            "<th style='padding:10px;text-align:left;'>🇨🇳 中文</th>"
            "<th style='padding:10px;text-align:left;'>🇬🇧 英文</th>"
            "<th style='padding:10px;text-align:left;width:100px;'>🏷 领域</th>"
            "<th style='padding:10px;text-align:center;width:60px;'>操作</th>"
            "</tr></thead><tbody>"
            + "".join(
                f"<tr style='border-bottom:1px solid #eee;'>"
                f"<td style='padding:8px;vertical-align:top;'>{a['source_text']}</td>"
                f"<td style='padding:8px;vertical-align:top;'>{a['target_text']}</td>"
                f"<td style='padding:8px;vertical-align:top;white-space:nowrap;'>{a['domain']}</td>"
                f"<td style='padding:8px;text-align:center;vertical-align:top;'>"
                f"</td>"
                f"</tr>"
                for a in assets
            )
            + "</tbody></table></div>"
        )
        st.markdown(table_html, unsafe_allow_html=True)

        # ── CSV 导出 ──
        csv_buffer = io.StringIO(newline="")
        writer = csv.writer(csv_buffer, quoting=csv.QUOTE_ALL)
        writer.writerow(["中文", "英文", "领域"])
        for a in assets:
            writer.writerow([a["source_text"], a["target_text"], a["domain"]])
        csv_bytes = csv_buffer.getvalue().encode("utf-8-sig")
        import base64
        st.markdown(
            f'<a href="data:text/csv;charset=utf-8;base64,{base64.b64encode(csv_bytes).decode()}" '
            f'download="terminology.csv" '
            f'style="display:inline-block;padding:6px 16px;background:#4CAF50;color:#fff;'
            f'text-decoration:none;border-radius:6px;font-size:14px;">📥 下载术语 CSV</a>',
            unsafe_allow_html=True,
        )

        # 批量删除
        st.divider()
        st.markdown("### 🗑 删除术语")

        delete_options = {f"#{a['id']} | {a['source_text']} → {a['target_text']}": a["id"] for a in assets}
        selected_labels = st.multiselect(
            "选择要删除的术语（可多选）",
            options=list(delete_options.keys()),
            key="term_delete_select",
        )

        col_del, _ = st.columns([1, 3])
        with col_del:
            if selected_labels:
                if st.button(f"🗑 删除选中 ({len(selected_labels)})", type="secondary", use_container_width=True):
                    for label in selected_labels:
                        delete_asset(delete_options[label])
                    st.success(f"已删除 {len(selected_labels)} 条术语")
                    st.rerun()

# ═══════════════════════════════════════════════════════
#  Tab 4：领域术语 — 领域检测 + 术语标注 + Prompt 预览
# ═══════════════════════════════════════════════════════
with tab4:
    st.subheader("🔬 领域术语 — 自动检测 + 术语标注 + Prompt 生成")

    # ── 输入测试文本 ──
    test_text = st.text_area(
        "输入文本进行领域检测和术语匹配",
        placeholder="在此粘贴需要分析的中文或英文文本...",
        height=150,
        key="term_test_text",
    )

    if test_text.strip():
        # ── 领域检测 ──
        detected = detect_domains(test_text, DOMAIN_KEYWORDS)

        st.divider()
        st.markdown("### 🎯 领域检测")

        if not detected:
            st.info("未能自动判断领域，将尝试匹配全部术语库")
            best_domain = "其他"
            active_domains = list(DOMAIN_KEYWORDS.keys())
        else:
            best_domain = detected[0][0]
            active_domains = [d for d, _ in detected]

            # 显示各领域得分
            cols = st.columns(len(detected))
            for i, (domain, score) in enumerate(detected):
                with cols[i]:
                    st.metric(f"🏷 {domain}", f"{score} 词匹配")

        # ── 术语匹配 ──
        terminology = load_app_terminology()
        active_domains = [normalize_domain(d) for d in active_domains]
        active_terms: list[tuple[str, str]] = []
        for d in active_domains:
            active_terms.extend(get_terms_for_domain(d, terminology))

        if active_terms:
            matched_terms, match_positions = match_terms(test_text, active_terms)

            st.divider()
            st.markdown(f"### 📋 术语匹配 — 共 {len(matched_terms)} 个术语")

            if matched_terms:
                # 术语对照表
                term_table_html = (
                    "<table style='width:100%;border-collapse:collapse;font-size:14px;'>"
                    "<tr style='background:#e0e0e0;'><th style='padding:8px;text-align:left;'>🇨🇳 中文</th><th style='padding:8px;text-align:left;'>🇬🇧 英文</th></tr>"
                    + "".join(
                        f"<tr style='border-bottom:1px solid #eee;'><td style='padding:8px;'>{ch}</td><td style='padding:8px;'>{en}</td></tr>"
                        for ch, en in matched_terms
                    )
                    + "</table>"
                )
                st.markdown(term_table_html, unsafe_allow_html=True)
            else:
                st.info("未匹配到术语，可尝试手动补充")
        else:
            matched_terms = []
            st.info("术语库为空或未覆盖该领域")

        # ── 系统 Prompt 预览 ──
        st.divider()
        st.markdown("### 📝 系统 Prompt 预览")
        st.caption("以下是翻译时自动生成的 System Prompt 结构：")

        system_prompt = build_domain_prompt(
            text=test_text,
            domain=best_domain if best_domain != "其他" else None,
            matched_terms=matched_terms if matched_terms else None,
        )

        with st.expander("查看完整 Prompt", expanded=True):
            st.code(system_prompt, language="markdown")

    else:
        st.info("👆 在上方输入文本，自动进行领域检测、术语匹配和 Prompt 生成")

    # ── 术语库概况 ──
    st.divider()
    st.markdown("### 📊 术语库概况")

    terminology = load_app_terminology()
    if terminology:
        total_terms = sum(len(v) for v in terminology.values())
        cols = st.columns(len(terminology) + 1)
        with cols[0]:
            st.metric("📊 总术语", total_terms)
        for i, (domain, terms) in enumerate(sorted(terminology.items())):
            with cols[i + 1]:
                st.metric(f"🏷 {domain}", len(terms))
    else:
        st.warning("未找到可用术语，请先在「术语库」上传 CSV 或检查静态术语文件路径")

    # ── 领域语调一览 ──
    st.divider()
    st.markdown("### 🎨 领域语调配置")

    for domain, tone in DOMAIN_TONES.items():
        st.markdown(f"- **{domain}**：{tone}")

# ── 规则发现弹窗（全局）──────────────────────────────
if st.session_state.style_prompt:
    pd_data = st.session_state.style_prompt
    st.divider()
    st.warning(f"🔍 **发现稳定修订模式**\n\n以下相同修改已出现 **{pd_data['count']} 次**：\n\n**before_text**\n> {pd_data['original']}\n\n**after_text**\n> {pd_data['modified']}\n\n句对已自动存入 **术语库**。")
    col1, col2, col3 = st.columns([1, 1, 1])
    with col1:
        if st.button("确认", type="primary", use_container_width=True): confirm_rule(pd_data["rule_id"]); st.session_state.style_prompt = None; st.success("已确认 🎉"); st.rerun()
    with col2:
        if st.button("忽略", use_container_width=True): ignore_rule(pd_data["rule_id"]); st.session_state.style_prompt = None; st.rerun()
    with col3:
        if st.button("以后再说", use_container_width=True): defer_rule(pd_data["rule_id"]); st.session_state.style_prompt = None; st.rerun()
