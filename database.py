"""数据库模块 - SQLite 操作（修改记录 + 候选规则 + 个人风格库）"""

import sqlite3
from datetime import datetime
from config import DB_PATH


def get_connection() -> sqlite3.Connection:
    """获取数据库连接"""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """初始化数据库表"""
    conn = get_connection()
    cursor = conn.cursor()

    # 修改记录表：记录每次用户对译文的修改
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS modifications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_text TEXT NOT NULL,
            original_translation TEXT NOT NULL,
            modified_translation TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # 候选规则表：检测到的翻译修改模式（含确认/忽略/稍后状态）
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS candidate_rules (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            original_phrase TEXT NOT NULL,
            preferred_phrase TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            count INTEGER NOT NULL DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # 个人风格库表：用户确认的翻译偏好（等同于 status='confirmed' 的规则）
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS style_library (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            original_phrase TEXT NOT NULL,
            preferred_phrase TEXT NOT NULL,
            confirmed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # 翻译记忆库表：存储已确认的翻译句对
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS memory_assets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_text TEXT NOT NULL,
            target_text TEXT NOT NULL,
            domain TEXT DEFAULT '其他',
            asset_type TEXT DEFAULT 'translation_memory',
            status TEXT DEFAULT 'active',
            created_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # 迁移：为旧表补加 status 列（如果不存在）
    try:
        cursor.execute("ALTER TABLE memory_assets ADD COLUMN status TEXT DEFAULT 'active'")
    except:
        pass  # 列已存在则忽略

    # 文件上传历史表
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS file_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            filename TEXT NOT NULL,
            sentence_count INTEGER DEFAULT 0,
            uploaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Prompt 覆写表：存储用户自定义的提示词（按模板 key 覆写）
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS prompt_overrides (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            template_key TEXT NOT NULL UNIQUE,
            prompt_text TEXT NOT NULL,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    conn.commit()
    conn.close()


# ═══════════════════════════════════════════════════════════════
#  Prompt 覆写（用户自定义提示词）
# ═══════════════════════════════════════════════════════════════

def get_prompt_override(template_key: str) -> str | None:
    """获取某个模板的用户覆写提示词，没有则返回 None"""
    conn = get_connection()
    row = conn.execute(
        "SELECT prompt_text FROM prompt_overrides WHERE template_key = ?",
        (template_key,),
    ).fetchone()
    conn.close()
    return row["prompt_text"] if row else None


def save_prompt_override(template_key: str, prompt_text: str):
    """保存或更新某个模板的用户覆写提示词"""
    conn = get_connection()
    conn.execute(
        """INSERT INTO prompt_overrides (template_key, prompt_text, updated_at)
           VALUES (?, ?, CURRENT_TIMESTAMP)
           ON CONFLICT(template_key) DO UPDATE SET
           prompt_text = excluded.prompt_text,
           updated_at = CURRENT_TIMESTAMP""",
        (template_key, prompt_text),
    )
    conn.commit()
    conn.close()


def delete_prompt_override(template_key: str):
    """删除某个模板的用户覆写，恢复默认提示词"""
    conn = get_connection()
    conn.execute(
        "DELETE FROM prompt_overrides WHERE template_key = ?",
        (template_key,),
    )
    conn.commit()
    conn.close()


def get_all_prompt_overrides() -> list[dict]:
    """获取所有用户覆写的提示词"""
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM prompt_overrides ORDER BY updated_at DESC"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ═══════════════════════════════════════════════════════════════
#  修改记录
# ═══════════════════════════════════════════════════════════════

def save_modification(source: str, original: str, modified: str):
    """保存一次译文修改记录"""
    conn = get_connection()
    conn.execute(
        "INSERT INTO modifications (source_text, original_translation, modified_translation) VALUES (?, ?, ?)",
        (source, original, modified),
    )
    conn.commit()
    conn.close()


def get_same_modification_count(original: str, modified: str) -> int:
    """统计相同的 original→modified 修改出现了多少次"""
    conn = get_connection()
    cursor = conn.execute(
        "SELECT COUNT(*) as cnt FROM modifications WHERE original_translation = ? AND modified_translation = ?",
        (original, modified),
    )
    result = cursor.fetchone()
    conn.close()
    return result["cnt"] if result else 0


def get_modification_history(limit: int = 50) -> list[dict]:
    """获取最近的修改记录"""
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM modifications ORDER BY created_at DESC LIMIT ?",
        (limit,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ═══════════════════════════════════════════════════════════════
#  候选规则
# ═══════════════════════════════════════════════════════════════

def upsert_candidate_rule(original: str, modified: str) -> dict:
    """
    创建或更新候选规则。
    返回完整规则信息，含 status、count、id。
    如果已存在（相同 original + preferred），递增 count。
    如果规则已被 ignored，不更新并返回 None。
    """
    conn = get_connection()

    # 查找是否已有该规则
    row = conn.execute(
        "SELECT * FROM candidate_rules WHERE original_phrase = ? AND preferred_phrase = ?",
        (original, modified),
    ).fetchone()

    if row:
        rule = dict(row)
        if rule["status"] == "ignored":
            conn.close()
            return None  # 已忽略，不再提示

        # 递增计数
        new_count = rule["count"] + 1
        conn.execute(
            "UPDATE candidate_rules SET count = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (new_count, rule["id"]),
        )
        conn.commit()
        conn.close()
        rule["count"] = new_count
        return rule
    else:
        # 新建规则
        cursor = conn.execute(
            "INSERT INTO candidate_rules (original_phrase, preferred_phrase, status, count) VALUES (?, ?, 'pending', 1)",
            (original, modified),
        )
        conn.commit()
        rule_id = cursor.lastrowid
        new_row = conn.execute("SELECT * FROM candidate_rules WHERE id = ?", (rule_id,)).fetchone()
        conn.close()
        return dict(new_row)


def update_rule_status(rule_id: int, status: str):
    """
    更新规则状态。
    status: 'confirmed' → 规则确认（句对已在保存时自动存入 memory_assets）
            'ignored'   → 不再提示
            'later'     → 稍后提醒
    """
    conn = get_connection()
    conn.execute(
        "UPDATE candidate_rules SET status = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
        (status, rule_id),
    )
    conn.commit()
    conn.close()


def get_pending_later_rules() -> list[dict]:
    """获取状态为 pending 或 later 的规则（用于重启后重新提示）"""
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM candidate_rules WHERE status IN ('pending', 'later') ORDER BY updated_at DESC"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_rule_by_id(rule_id: int) -> dict | None:
    """根据 ID 获取规则"""
    conn = get_connection()
    row = conn.execute("SELECT * FROM candidate_rules WHERE id = ?", (rule_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


# ═══════════════════════════════════════════════════════════════
#  个人风格库
# ═══════════════════════════════════════════════════════════════

def add_to_style_library(original_phrase: str, preferred_phrase: str):
    """将翻译偏好添加到个人风格库（直接调用，不走候选规则流程）"""
    conn = get_connection()
    existing = conn.execute(
        "SELECT id FROM style_library WHERE original_phrase = ? AND preferred_phrase = ?",
        (original_phrase, preferred_phrase),
    ).fetchone()

    if not existing:
        conn.execute(
            "INSERT INTO style_library (original_phrase, preferred_phrase) VALUES (?, ?)",
            (original_phrase, preferred_phrase),
        )
        conn.commit()

    conn.close()


def get_style_library() -> list[dict]:
    """获取个人风格库所有条目"""
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM style_library ORDER BY confirmed_at DESC"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ═══════════════════════════════════════════════════════════════
#  翻译记忆库 (memory_assets)
# ═══════════════════════════════════════════════════════════════

def get_all_assets(
    domain: str = None,
    status: str = None,
    keyword: str = None,
    limit: int = 500,
) -> list[dict]:
    """获取翻译记忆库条目，支持筛选和搜索"""
    conn = get_connection()
    query = "SELECT * FROM memory_assets WHERE 1=1"
    params = []

    if domain:
        query += " AND domain = ?"
        params.append(domain)
    if status:
        query += " AND status = ?"
        params.append(status)
    if keyword:
        query += " AND (source_text LIKE ? OR target_text LIKE ?)"
        kw = f"%{keyword}%"
        params.extend([kw, kw])

    query += " ORDER BY updated_time DESC LIMIT ?"
    params.append(limit)

    rows = conn.execute(query, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_asset_statuses() -> list[str]:
    """获取所有不重复的 status 值"""
    conn = get_connection()
    rows = conn.execute(
        "SELECT DISTINCT status FROM memory_assets ORDER BY status"
    ).fetchall()
    conn.close()
    return [r["status"] for r in rows]


def update_asset_status(asset_id: int, status: str):
    """更新一条翻译记忆的状态"""
    conn = get_connection()
    conn.execute(
        "UPDATE memory_assets SET status = ?, updated_time = CURRENT_TIMESTAMP WHERE id = ?",
        (status, asset_id),
    )
    conn.commit()
    conn.close()


def get_asset_stats() -> dict:
    """获取记忆库统计信息"""
    conn = get_connection()
    total = conn.execute("SELECT COUNT(*) as cnt FROM memory_assets").fetchone()["cnt"]
    active = conn.execute("SELECT COUNT(*) as cnt FROM memory_assets WHERE status='active'").fetchone()["cnt"]
    conn.close()
    return {"total": total, "active": active}


def get_asset_domains() -> list[str]:
    """获取所有不重复的 domain 值"""
    conn = get_connection()
    rows = conn.execute(
        "SELECT DISTINCT domain FROM memory_assets ORDER BY domain"
    ).fetchall()
    conn.close()
    return [r["domain"] for r in rows]


def insert_asset(source: str, target: str, domain: str = "其他") -> int:
    """插入一条翻译记忆，返回新记录的 ID"""
    conn = get_connection()
    cursor = conn.execute(
        "INSERT INTO memory_assets (source_text, target_text, domain) VALUES (?, ?, ?)",
        (source, target, domain),
    )
    conn.commit()
    new_id = cursor.lastrowid
    conn.close()
    return new_id


def update_asset(asset_id: int, source: str, target: str, domain: str):
    """更新一条翻译记忆"""
    conn = get_connection()
    conn.execute(
        "UPDATE memory_assets SET source_text=?, target_text=?, domain=?, updated_time=CURRENT_TIMESTAMP WHERE id=?",
        (source, target, domain, asset_id),
    )
    conn.commit()
    conn.close()


def delete_asset(asset_id: int):
    """删除一条翻译记忆"""
    conn = get_connection()
    conn.execute("DELETE FROM memory_assets WHERE id = ?", (asset_id,))
    conn.commit()
    conn.close()


def get_asset_count() -> int:
    """获取记忆库总条目数"""
    conn = get_connection()
    row = conn.execute("SELECT COUNT(*) as cnt FROM memory_assets").fetchone()
    conn.close()
    return row["cnt"] if row else 0


# ═══════════════════════════════════════════════════════════════
#  文件上传历史
# ═══════════════════════════════════════════════════════════════

def add_file_history(filename: str, sentence_count: int) -> int:
    """记录一次文件上传，返回记录 ID"""
    conn = get_connection()
    cursor = conn.execute(
        "INSERT INTO file_history (filename, sentence_count) VALUES (?, ?)",
        (filename, sentence_count),
    )
    conn.commit()
    new_id = cursor.lastrowid
    conn.close()
    return new_id


def get_file_history(limit: int = 20) -> list[dict]:
    """获取文件上传历史"""
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM file_history ORDER BY uploaded_at DESC LIMIT ?",
        (limit,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def delete_file_history(file_id: int):
    """删除一条文件历史记录"""
    conn = get_connection()
    conn.execute("DELETE FROM file_history WHERE id = ?", (file_id,))
    conn.commit()
    conn.close()
