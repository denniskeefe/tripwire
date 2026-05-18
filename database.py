import sqlite3
import json
from datetime import datetime
from config import DB_PATH


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_db()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS targets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            url TEXT NOT NULL,
            label TEXT NOT NULL,
            platform TEXT NOT NULL DEFAULT 'generic',
            check_interval INTEGER NOT NULL DEFAULT 15,
            active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            last_checked TEXT,
            last_changed TEXT,
            status TEXT DEFAULT 'pending'
        );

        CREATE TABLE IF NOT EXISTS snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            target_id INTEGER NOT NULL,
            content_hash TEXT NOT NULL,
            content_excerpt TEXT,
            checked_at TEXT NOT NULL,
            FOREIGN KEY (target_id) REFERENCES targets(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS alerts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            target_id INTEGER NOT NULL,
            target_label TEXT NOT NULL,
            target_url TEXT NOT NULL,
            change_summary TEXT,
            detected_at TEXT NOT NULL,
            email_sent INTEGER DEFAULT 0,
            FOREIGN KEY (target_id) REFERENCES targets(id) ON DELETE CASCADE
        );
    """)
    conn.commit()
    conn.close()


def add_target(url, label, platform, check_interval):
    conn = get_db()
    now = datetime.utcnow().isoformat()
    cur = conn.execute(
        "INSERT INTO targets (url, label, platform, check_interval, created_at) VALUES (?, ?, ?, ?, ?)",
        (url, label, platform, check_interval, now),
    )
    target_id = cur.lastrowid
    conn.commit()
    conn.close()
    return target_id


def get_targets(active_only=False):
    conn = get_db()
    if active_only:
        rows = conn.execute("SELECT * FROM targets WHERE active = 1 ORDER BY created_at DESC").fetchall()
    else:
        rows = conn.execute("SELECT * FROM targets ORDER BY created_at DESC").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_target(target_id):
    conn = get_db()
    row = conn.execute("SELECT * FROM targets WHERE id = ?", (target_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def delete_target(target_id):
    conn = get_db()
    conn.execute("DELETE FROM targets WHERE id = ?", (target_id,))
    conn.commit()
    conn.close()


def toggle_target(target_id, active):
    conn = get_db()
    conn.execute("UPDATE targets SET active = ? WHERE id = ?", (1 if active else 0, target_id))
    conn.commit()
    conn.close()


def get_latest_snapshot(target_id):
    conn = get_db()
    row = conn.execute(
        "SELECT * FROM snapshots WHERE target_id = ? ORDER BY checked_at DESC LIMIT 1",
        (target_id,),
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def save_snapshot(target_id, content_hash, content_excerpt):
    conn = get_db()
    now = datetime.utcnow().isoformat()
    conn.execute(
        "INSERT INTO snapshots (target_id, content_hash, content_excerpt, checked_at) VALUES (?, ?, ?, ?)",
        (target_id, content_hash, content_excerpt[:2000] if content_excerpt else "", now),
    )
    conn.execute(
        "UPDATE targets SET last_checked = ?, status = 'ok' WHERE id = ?",
        (now, target_id),
    )
    conn.commit()
    conn.close()


def mark_target_error(target_id, error_msg):
    conn = get_db()
    now = datetime.utcnow().isoformat()
    conn.execute(
        "UPDATE targets SET last_checked = ?, status = ? WHERE id = ?",
        (now, f"error: {error_msg[:200]}", target_id),
    )
    conn.commit()
    conn.close()


def record_alert(target_id, label, url, change_summary, email_sent=False):
    conn = get_db()
    now = datetime.utcnow().isoformat()
    conn.execute(
        "INSERT INTO alerts (target_id, target_label, target_url, change_summary, detected_at, email_sent) VALUES (?, ?, ?, ?, ?, ?)",
        (target_id, label, url, change_summary, now, 1 if email_sent else 0),
    )
    conn.execute(
        "UPDATE targets SET last_changed = ?, status = 'changed' WHERE id = ?",
        (now, target_id),
    )
    conn.commit()
    conn.close()


def get_alerts(limit=50):
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM alerts ORDER BY detected_at DESC LIMIT ?", (limit,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]
