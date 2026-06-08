"""翻译记忆学习系统 - Streamlit 主应用"""

import io
import csv
import streamlit as st
from translator import translate, PROVIDER_LABELS, DIRECTION_LABELS
from config import DEFAULT_PROVIDER
from database import init_db, get_prompt_override, save_prompt_override, delete_prompt_override, get_all_prompt_overrides
from tracker import record_modification, confirm_rule, ignore_rule, defer_rule
from document import parse_uploaded_file, filter_chinese_only, filter_english_only
from prompt_center import (
    STYLE_LABELS, STYLE_CONFIGS,
    get_default_prompt_text, get_effective_principles,
)

st.set_page_config(page_title="翻译记忆学习系统", page_icon="🌐", layout="wide")
init_db()

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
    st.session_state.app_domain = "其他"
if "prompt_template" not in st.session_state:
    st.session_state.prompt_template = "default"
if "custom_prompt_text" not in st.session_state:
    st.session_state.custom_prompt_text = ""
if "last_retrieval" not in st.session_state:
    st.session_state.last_retrieval = None

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

    st.divider()
    from prompt_center import PROMPT_LABELS
    st.session_state.prompt_template = st.selectbox(
        "Prompt",
        options=list(PROMPT_LABELS.keys()),
        format_func=lambda k: PROMPT_LABELS[k],
        key="prompt_sidebar",
    )
    if st.session_state.prompt_template == "custom":
        st.session_state.custom_prompt_text = st.text_area(
            "自定义", value=st.session_state.custom_prompt_text or "",
            height=100, label_visibility="collapsed",
        )

# ── 主界面 ────────────────────────────────────────────
st.title("🌐 翻译记忆学习系统")

tab1, tab2, tab3, tab4 = st.tabs(["📄 文档翻译", "📁 我的文件", "📚 记忆库", "🎨 Prompt"])

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
                    "Domain", options=["经济金融", "传统文化", "政治外交", "化学化工", "教育", "法律", "医学", "其他"], key="doc_domain",
                )
            with col_btn:
                st.write(""); st.write("")
                translate_all_clicked = st.button("🌐 翻译全部", type="primary", use_container_width=True)

            if translate_all_clicked:
                st.session_state.doc_translations = {}
                st.session_state.last_retrieval = None
                progress_bar = st.progress(0)
                total = len(display_segments)
                for i, seg in enumerate(display_segments):
                    try:
                        translation, retrieval = translate(
                            seg["source_text"], provider=st.session_state.provider,
                            direction=st.session_state.doc_direction,
                            prompt_template=st.session_state.prompt_template,
                            custom_prompt=st.session_state.custom_prompt_text,
                        )
                        st.session_state.doc_translations[seg["sentence_id"]] = translation
                        if retrieval:
                            st.session_state.last_retrieval = retrieval
                    except Exception as e:
                        st.session_state.doc_translations[seg["sentence_id"]] = f"❌ {e}"
                    progress_bar.progress((i + 1) / total)
                st.success(f"翻译完成！共 {total} 句")

            # ── 检索结果展示 ────────────────────────────
            if st.session_state.get("last_retrieval"):
                r = st.session_state.last_retrieval
                with st.expander(
                    f"🔍 本次翻译使用 · 模型：{PROVIDER_LABELS[st.session_state.provider]} · "
                    f"Prompt：{st.session_state.prompt_template} · "
                    f"记忆库命中：{r['count']}",
                    expanded=False,
                ):
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
                                    insert_asset(source=seg["source_text"], target=modified, domain=st.session_state.app_domain)
                                st.session_state[f"saved_{sid}"] = True
                                st.success(f"{sid} 已保存 → Memory Base")
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
                                insert_asset(source=seg["source_text"], target=modified, domain=st.session_state.app_domain)
                            st.session_state[f"saved_{sid}"] = True
                            saved_count += 1
                        st.session_state.doc_editing = False
                        if last_prompt:
                            st.session_state.style_prompt = last_prompt
                        st.success(f"已保存 {saved_count} 条 → Memory Base")
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
#  Tab 3：记忆库
# ═══════════════════════════════════════════════════════
with tab3:
    from database import get_all_assets, get_asset_stats, delete_asset, insert_asset

    stats = get_asset_stats()
    m1, m2 = st.columns(2)
    with m1: st.metric("📊 Total Assets", stats["total"])
    with m2: st.metric("✅ Active", stats.get("active", stats["total"]))

    # Import
    with st.expander("📥 Import Excel / CSV", expanded=False):
        imp_file = st.file_uploader("上传", type=["xlsx", "csv"], key="mb_import_file", label_visibility="collapsed")
        if imp_file and st.button("📥 开始导入", type="primary", use_container_width=True):
            try:
                fn = imp_file.name.lower()
                if fn.endswith(".csv"):
                    rows = list(csv.DictReader(io.StringIO(imp_file.read().decode("utf-8-sig"))))
                elif fn.endswith(".xlsx"):
                    from openpyxl import load_workbook
                    wb = load_workbook(imp_file, read_only=True); ws = wb.active
                    headers = [c.value for c in next(ws.iter_rows(min_row=1, max_row=1))]
                    rows = [dict(zip(headers, r)) for r in ws.iter_rows(min_row=2, values_only=True)]
                    wb.close()
                else:
                    st.error("不支持的文件格式"); rows = []
                if rows:
                    missing = {"source_text", "target_text"} - set(rows[0].keys())
                    if missing:
                        st.error(f"❌ 缺少必须字段：{', '.join(missing)}")
                    else:
                        imported = skipped = 0
                        for r in rows:
                            src = (r.get("source_text") or "").strip()
                            tgt = (r.get("target_text") or "").strip()
                            if not src or not tgt: skipped += 1; continue
                            insert_asset(source=src, target=tgt, domain=(r.get("domain") or "其他").strip())
                            imported += 1
                        st.success(f"✅ 导入 {imported} 条" + (f"，跳过 {skipped} 条" if skipped else "")); st.rerun()
            except Exception as e:
                st.error(f"导入失败：{e}")

    # Search + filters
    c1, c2, c3 = st.columns(3)
    with c1: keyword = st.text_input("🔍 搜索", key="mb_search")
    with c2: domain_filter = st.selectbox("Domain", options=["全部", "经济金融", "传统文化", "政治外交", "化学化工", "教育", "法律", "医学", "其他"], key="mb_domain")
    with c3: status_filter = st.selectbox("Status", options=["全部", "active", "draft", "archived"], key="mb_status")

    assets = get_all_assets(
        domain=None if domain_filter == "全部" else domain_filter,
        status=None if status_filter == "全部" else status_filter,
        keyword=keyword.strip() or None,
    )

    selected_indices = []
    if "mb_selected_rows" in st.session_state:
        sel = st.session_state.mb_selected_rows
        if isinstance(sel, dict) and "selection" in sel:
            selected_indices = sel["selection"].get("rows", [])
        elif hasattr(sel, "selection") and hasattr(sel.selection, "rows"):
            selected_indices = sel.selection.rows

    ci, cd = st.columns([4, 1])
    with ci: st.markdown(f"**共 {len(assets)} 条**（总计 {stats['total']} 条）")
    with cd:
        if assets and selected_indices:
            if st.button(f"🗑 删除 ({len(selected_indices)})", type="secondary", use_container_width=True):
                for idx in selected_indices: delete_asset(assets[idx]["id"])
                st.session_state.mb_selected_rows = {}
                st.success(f"已删除 {len(selected_indices)} 条"); st.rerun()

    if not assets:
        st.info("🙅 No Assets Found.")
    else:
        import pandas as pd
        df = pd.DataFrame([{"ID": a["id"], "Source Text": a["source_text"], "Target Text": a["target_text"], "Domain": a["domain"], "Status": a["status"], "Updated Time": a["updated_time"]} for a in assets])
        st.dataframe(df, use_container_width=True, height=550, hide_index=True, selection_mode="multi-row", on_select="rerun", key="mb_selected_rows")

# ═══════════════════════════════════════════════════════
#  Tab 4：Prompt 管理
# ═══════════════════════════════════════════════════════
with tab4:
    st.subheader("🎨 Prompt 管理")

    st.markdown("选择领域 / 风格，查看和编辑对应的提示词。保存后将**持久生效**，用于所有翻译。")

    # ── 选择领域 ────────────────────────────────────────
    col_sel, col_act = st.columns([2, 1])
    with col_sel:
        selected_style = st.selectbox(
            "选择领域 / 风格",
            options=list(STYLE_LABELS.keys()),
            format_func=lambda k: STYLE_LABELS[k],
            key="prompt_mgmt_style",
        )

    config = STYLE_CONFIGS.get(selected_style, STYLE_CONFIGS["default"])

    # ── 显示当前生效的提示词 ────────────────────────────
    st.divider()

    # 获取默认提示词文本和用户覆写
    default_text = get_default_prompt_text(selected_style)
    saved_override = get_prompt_override(selected_style)

    # 当前实际使用的原则
    current_principles = get_effective_principles(selected_style)
    has_override = saved_override is not None

    if has_override:
        st.success(f"✅ **{STYLE_LABELS[selected_style]}** — 当前使用自定义提示词")
    else:
        st.info(f"📋 **{STYLE_LABELS[selected_style]}** — 当前使用默认提示词")

    # ── 提示词展示 / 编辑区 ─────────────────────────────
    st.markdown("### ✏️ 翻译原则")

    # 编辑内容：优先展示用户覆写，否则展示默认
    if has_override:
        edit_text = saved_override
    else:
        # 将默认原则格式化为可编辑文本
        edit_text = "\n".join(config.get("principles", []))

    edited_principles = st.text_area(
        "翻译原则（每行一条）",
        value=edit_text,
        height=250,
        key="prompt_mgmt_editor",
        help="每行一条翻译原则。保存后将覆盖默认设置，用于该领域的所有翻译。",
    )

    # ── 操作按钮 ────────────────────────────────────────
    col_save, col_reset, col_preview = st.columns([1, 1, 2])

    with col_save:
        if st.button("💾 保存提示词", type="primary", use_container_width=True, key="prompt_mgmt_save"):
            trimmed = edited_principles.strip()
            if trimmed:
                save_prompt_override(selected_style, trimmed)
                st.success(f"✅ **{STYLE_LABELS[selected_style]}** 提示词已保存！")
                st.rerun()
            else:
                st.error("提示词不能为空")

    with col_reset:
        if has_override:
            if st.button("🔄 恢复默认", use_container_width=True, key="prompt_mgmt_reset"):
                delete_prompt_override(selected_style)
                st.success(f"🔄 **{STYLE_LABELS[selected_style]}** 已恢复默认提示词")
                st.rerun()
        else:
            st.button("🔄 恢复默认", disabled=True, use_container_width=True, key="prompt_mgmt_reset_disabled")

    # ── 预览区 ──────────────────────────────────────────
    st.divider()
    st.markdown("### 🔍 最终 System Prompt 预览")
    st.caption("以下是该风格在翻译时实际组装出的 System Prompt 结构：")

    with st.expander("查看完整预览", expanded=False):
        # 模拟组装一个预览
        preview_parts = []
        preview_parts.append(f"## Domain\n（自动识别，如：经济金融）")
        preview_parts.append(f"## Target Audience\n{config.get('audience', 'Professional Readers')}")
        preview_parts.append(f"## Style\n{config.get('style_name', selected_style)}")
        preview_parts.append(f"## Terminology\n（术语表内容，如有）")
        preview_parts.append(f"## Translation Memory\n（从记忆库检索到的相关句对，如有）")
        preview_parts.append(f"## Style Examples\n（风格示例，如有）")

        principles = get_effective_principles(selected_style)
        principles_lines = ["## Translation Principles"]
        for i, p in enumerate(principles, 1):
            principles_lines.append(f"{i}. {p}")
        preview_parts.append("\n".join(principles_lines))

        preview_text = "\n\n".join(preview_parts)
        st.code(preview_text, language="markdown")

    # ── 所有覆写一览 ────────────────────────────────────
    st.divider()
    st.markdown("### 📋 所有自定义提示词")
    all_overrides = get_all_prompt_overrides()
    if all_overrides:
        for o in all_overrides:
            tk = o["template_key"]
            label = STYLE_LABELS.get(tk, tk)
            st.caption(f"**{label}** — 更新于 {o['updated_at']}")
    else:
        st.caption("暂无自定义提示词，所有领域使用默认设置。")

# ── 规则发现弹窗（全局）──────────────────────────────
if st.session_state.style_prompt:
    pd_data = st.session_state.style_prompt
    st.divider()
    st.warning(f"🔍 **发现稳定修订模式**\n\n以下相同修改已出现 **{pd_data['count']} 次**：\n\n**before_text**\n> {pd_data['original']}\n\n**after_text**\n> {pd_data['modified']}\n\n句对已自动存入 **Memory Base**。")
    col1, col2, col3 = st.columns([1, 1, 1])
    with col1:
        if st.button("确认", type="primary", use_container_width=True): confirm_rule(pd_data["rule_id"]); st.session_state.style_prompt = None; st.success("已确认 🎉"); st.rerun()
    with col2:
        if st.button("忽略", use_container_width=True): ignore_rule(pd_data["rule_id"]); st.session_state.style_prompt = None; st.rerun()
    with col3:
        if st.button("以后再说", use_container_width=True): defer_rule(pd_data["rule_id"]); st.session_state.style_prompt = None; st.rerun()
