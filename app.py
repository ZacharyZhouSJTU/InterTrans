"""翻译记忆学习系统 - Streamlit 主应用"""

import io
import csv
import inspect
import re
from pathlib import Path
import streamlit as st
from translator import translate, PROVIDER_LABELS, DIRECTION_LABELS
from config import DEFAULT_PROVIDER
from database import init_db
from tracker import record_modification, confirm_rule, ignore_rule, defer_rule
from document import parse_uploaded_file, filter_chinese_only, filter_english_only

# term_manager 是可选模块：本地有则优先使用；Streamlit Cloud 缺少该文件时启用内置兜底实现，
# 避免 ModuleNotFoundError 直接导致网页无法启动。
try:
    from term_manager import (
        detect_domains, load_terminology,
        get_terms_for_domain, match_terms,
        build_domain_prompt, DOMAIN_KEYWORDS, DOMAIN_TONES,
    )
    TERM_MANAGER_SOURCE = "term_manager.py"
except ModuleNotFoundError:
    TERM_MANAGER_SOURCE = "app.py 内置兜底"

    DOMAIN_KEYWORDS = {
        "医疗": [
            "患者", "诊断", "治疗", "手术", "药物", "症状", "临床", "病理",
            "医生", "护士", "医院", "病房", "处方", "剂量", "副作用", "康复",
            "影像", "检验", "体检", "急诊", "发热", "头痛", "咳嗽", "炎症",
            "麻醉", "切除", "移植", "疫苗", "感染", "肿瘤", "细胞", "血液",
        ],
        "法律": [
            "合同", "法律", "诉讼", "判决", "仲裁", "条款", "违约", "原告",
            "被告", "法院", "律师", "证据", "上诉", "赔偿", "知识产权", "专利",
            "商标", "法人", "债权", "债务", "抵押", "担保", "管辖", "起诉",
            "应诉", "调解", "裁定", "法条", "立法", "司法", "违法", "合法",
        ],
        "信息技术": [
            "信息技术", "人工智能", "生成式人工智能", "大模型", "大语言模型",
            "语义理解", "知识检索", "内容生成", "多轮对话", "逻辑推理",
            "检索增强生成", "RAG", "幻觉", "代码", "数据", "数据库", "算法",
            "接口", "前端", "后端", "云计算", "机器学习", "深度学习", "部署",
            "调试", "网络", "软件", "硬件", "编程", "系统", "架构", "自动化",
            "并发", "缓存", "容器", "微服务", "API", "SDK", "DevOps", "Python", "Java",
        ],
        "金融": [
            "股票", "基金", "利率", "汇率", "资产", "负债", "利润", "现金流",
            "分红", "贷款", "投资", "理财", "保险", "信用卡", "营收", "市盈率",
            "K线", "牛市", "熊市", "通胀", "通缩", "央行", "降息", "加息",
            "证券", "期货", "期权", "信托", "风投", "融资", "上市", "市值",
        ],
        "传统文化": ["儒家", "道家", "礼制", "诗词", "典故", "书法", "国画", "非遗", "民俗"],
        "政治外交": ["外交", "主权", "双边", "多边", "公报", "倡议", "治理", "国际关系"],
        "化学化工": ["反应", "催化", "溶液", "浓度", "化合物", "聚合", "萃取", "蒸馏"],
        "教育": ["课程", "教学", "学习", "考试", "评价", "课堂", "教材", "培养方案"],
    }

    DOMAIN_TONES = {
        "医疗": "极其严密、专业、中立，符合医学文献与临床手册规范，确保医学专有名词与诊疗表述准确",
        "法律": "严谨、客观、高度程式化，确保条文、责任与权利义务表述清晰",
        "信息技术": "简练、现代、注重逻辑，符合科技产品 UI、技术文档和开发者阅读习惯",
        "金融": "专业、严谨，符合财经行业合规性与时效性，准确传达财务和市场逻辑",
        "传统文化": "准确、雅正，保留文化负载词含义，必要时采用解释性翻译",
        "政治外交": "正式、稳健、中立，符合政策文本和外交表述规范",
        "化学化工": "精确、客观，符合化学化工专业文献表达",
        "教育": "清晰、规范，符合教育教学和学术文本表达",
        "其他": "专业、准确、自然，避免过度发挥",
    }

    def _candidate_terminology_paths() -> list[Path]:
        here = Path(__file__).resolve().parent
        return [
            here / "terminology.csv",
            here / "term_tool" / "terminology.csv",
            Path.cwd() / "terminology.csv",
            Path.cwd() / "term_tool" / "terminology.csv",
        ]

    def load_terminology(csv_path: str | None = None) -> dict[str, list[tuple[str, str]]]:
        """读取静态 CSV 术语。找不到文件时返回空字典，不中断网页启动。"""
        paths = [Path(csv_path)] if csv_path else _candidate_terminology_paths()
        path = next((p for p in paths if p and p.exists()), None)
        if path is None:
            return {}

        try:
            raw = path.read_text(encoding="utf-8-sig")
        except UnicodeDecodeError:
            raw = path.read_text(encoding="gbk", errors="replace")

        terminology: dict[str, list[tuple[str, str]]] = {}
        reader = csv.DictReader(io.StringIO(raw))
        headers = reader.fieldnames or []

        def pick(row: dict, *names: str) -> str:
            for name in names:
                if name in row and row[name] is not None:
                    return str(row[name]).strip()
            return ""

        for row in reader:
            source = pick(row, "中文术语", "中文", "source_text", "source", "zh", "cn")
            target = pick(row, "英文术语", "英文", "target_text", "target", "en", "english")
            domain = pick(row, "领域", "domain", "field", "category") or "其他"
            if source and target:
                terminology.setdefault(domain, []).append((source, target))
        return terminology

    def detect_domains(text: str, keywords: dict[str, list[str]]) -> list[tuple[str, int]]:
        scores: dict[str, int] = {}
        low_text = (text or "").lower()
        for domain, kw_list in keywords.items():
            score = 0
            for kw in kw_list:
                if not kw:
                    continue
                if re.search(r"[A-Za-z]", kw):
                    if kw.lower() in low_text:
                        score += 1
                elif kw in text:
                    score += 1
            if score > 0:
                scores[domain] = score
        return sorted(scores.items(), key=lambda x: x[1], reverse=True)

    def get_terms_for_domain(domain: str, terminology: dict[str, list[tuple[str, str]]]) -> list[tuple[str, str]]:
        if not domain:
            return []
        return list((terminology or {}).get(domain, []))

    def _term_in_text(text: str, source: str, target: str) -> bool:
        text = text or ""
        low_text = text.lower()
        source = source or ""
        target = target or ""
        if source and source in text:
            return True
        if target:
            pattern = r"(?<![A-Za-z])" + re.escape(target.lower()) + r"s?(?![A-Za-z])"
            if re.search(pattern, low_text):
                return True
        return False

    def match_terms(text: str, terms: list[tuple[str, str]]) -> tuple[list[tuple[str, str]], list[tuple[int, int, str, str]]]:
        """返回命中的术语和位置。位置主要供兼容展示使用。"""
        matched: list[tuple[str, str]] = []
        positions: list[tuple[int, int, str, str]] = []
        seen: set[tuple[str, str]] = set()
        for source, target in terms or []:
            source = (source or "").strip()
            target = (target or "").strip()
            if not source or not target:
                continue
            if not _term_in_text(text, source, target):
                continue
            key = (source.lower(), target.lower())
            if key not in seen:
                matched.append((source, target))
                seen.add(key)
            idx = (text or "").find(source)
            if idx >= 0:
                positions.append((idx, idx + len(source), source, target))
        return matched, positions

    def build_domain_prompt(
        text: str,
        domain: str | None = None,
        matched_terms: list[tuple[str, str]] | None = None,
    ) -> str:
        domain_name = domain or "通用"
        tone = DOMAIN_TONES.get(domain or "其他", DOMAIN_TONES["其他"])
        term_lines = "\n".join(f"  • {ch} → {en}" for ch, en in (matched_terms or []))
        if not term_lines:
            term_lines = "  （当前文本未命中术语库术语）"
        return f"""# Role
你是一位资深的{domain_name}领域翻译专家。

# Style & Domain
- 领域：{domain_name}
- 语调：{tone}
- 翻译必须忠实、准确、自然，避免自行扩写事实。

# Terminology & Rules
以下术语来自当前网页术语库；若原文出现对应概念，必须优先采用指定译法：
{term_lines}

# Task
请基于以上领域、语调和术语要求翻译用户输入文本。""".strip()

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
                    add_file_history(uploaded_file.name, len(st.session_state.segments))
                    st.success(f"解析完成！共 {len(st.session_state.segments)} 个句子")
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
