"""SQLite 저장소: 수집 항목, 수집 기록, 브리핑, 논문 분석 결과."""
import json
import os
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta

from .config import DATA_DIR

DB_PATH = os.path.join(DATA_DIR, "lab.db")
_lock = threading.RLock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    url TEXT UNIQUE,
    kind TEXT,            -- paper | news | issue | data
    source TEXT,
    topic TEXT,
    title TEXT,
    summary TEXT,
    authors TEXT,
    published TEXT,
    extra TEXT,
    fetched_at TEXT,
    starred INTEGER DEFAULT 0,
    is_read INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_items_fetched ON items(fetched_at);
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT, finished_at TEXT, status TEXT, log TEXT, new_count INTEGER
);
CREATE TABLE IF NOT EXISTS briefings (
    day TEXT PRIMARY KEY, content TEXT, engine TEXT, created_at TEXT
);
CREATE TABLE IF NOT EXISTS papers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    filename TEXT, stored_path TEXT, title TEXT, status TEXT, message TEXT,
    analysis TEXT, report_path TEXT, engine TEXT, created_at TEXT, source_item INTEGER
);
"""


@contextmanager
def conn():
    with _lock:
        c = sqlite3.connect(DB_PATH, timeout=30)
        c.row_factory = sqlite3.Row
        try:
            yield c
            c.commit()
        finally:
            c.close()


def init_db(reset_stale=False):
    with conn() as c:
        c.executescript(SCHEMA)
        if not reset_stale:
            return
        # 서버가 분석 도중 종료됐다면 상태를 정리
        c.execute("UPDATE papers SET status='error', message='서버 재시작으로 중단됨 - 다시 분석해 주세요' "
                  "WHERE status IN ('queued','running')")


def now():
    return datetime.now().isoformat(timespec="seconds")


def _row(r):
    d = dict(r)
    if "extra" in d:
        d["extra"] = json.loads(d["extra"] or "{}")
    if d.get("analysis"):
        d["analysis"] = json.loads(d["analysis"])
    return d


# ---------- items ----------
def upsert_items(items):
    """새 항목만 추가. 새로 추가된 항목 리스트 반환."""
    added = []
    with conn() as c:
        for it in items:
            if not it.get("url") or not it.get("title"):
                continue
            cur = c.execute(
                "INSERT OR IGNORE INTO items(url, kind, source, topic, title, summary, authors, published, extra, fetched_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,?)",
                (it["url"], it["kind"], it["source"], it.get("topic", ""), it["title"].strip(),
                 (it.get("summary") or "").strip()[:1200], it.get("authors", ""), it.get("published", ""),
                 json.dumps(it.get("extra", {}), ensure_ascii=False), now()),
            )
            if cur.rowcount:
                added.append(it)
    return added


def list_items(kind=None, topic=None, q=None, days=14, starred=False, limit=300):
    sql = "SELECT * FROM items WHERE 1=1"
    args = []
    if kind:
        sql += " AND kind=?"; args.append(kind)
    if topic:
        sql += " AND topic=?"; args.append(topic)
    if q:
        sql += " AND (title LIKE ? OR summary LIKE ?)"; args += [f"%{q}%", f"%{q}%"]
    if starred:
        sql += " AND starred=1"
    elif days:
        since = (datetime.now() - timedelta(days=days)).isoformat(timespec="seconds")
        sql += " AND fetched_at>=?"; args.append(since)
    sql += " ORDER BY fetched_at DESC, published DESC LIMIT ?"
    args.append(limit)
    with conn() as c:
        return [_row(r) for r in c.execute(sql, args)]


def items_since(iso_ts, limit=400):
    with conn() as c:
        return [_row(r) for r in c.execute(
            "SELECT * FROM items WHERE fetched_at>=? ORDER BY id LIMIT ?", (iso_ts, limit))]


def get_item(item_id):
    with conn() as c:
        r = c.execute("SELECT * FROM items WHERE id=?", (item_id,)).fetchone()
        return _row(r) if r else None


def update_item(item_id, **fields):
    allowed = {k: v for k, v in fields.items() if k in ("starred", "is_read")}
    if not allowed:
        return
    with conn() as c:
        sets = ", ".join(f"{k}=?" for k in allowed)
        c.execute(f"UPDATE items SET {sets} WHERE id=?", (*allowed.values(), item_id))


def stats():
    today = datetime.now().strftime("%Y-%m-%d")
    week = (datetime.now() - timedelta(days=7)).isoformat(timespec="seconds")
    with conn() as c:
        today_counts = {r["kind"]: r["n"] for r in c.execute(
            "SELECT kind, COUNT(*) n FROM items WHERE fetched_at>=? GROUP BY kind", (today,))}
        week_counts = {r["kind"]: r["n"] for r in c.execute(
            "SELECT kind, COUNT(*) n FROM items WHERE fetched_at>=? GROUP BY kind", (week,))}
        topics = [dict(r) for r in c.execute(
            "SELECT topic, COUNT(*) n FROM items WHERE fetched_at>=? AND topic!='' GROUP BY topic ORDER BY n DESC",
            (week,))]
        papers_done = c.execute("SELECT COUNT(*) FROM papers WHERE status='done'").fetchone()[0]
        starred = c.execute("SELECT COUNT(*) FROM items WHERE starred=1").fetchone()[0]
        last_run = c.execute("SELECT * FROM runs ORDER BY id DESC LIMIT 1").fetchone()
    return {
        "today": today_counts, "week": week_counts, "topics": topics,
        "papers_done": papers_done, "starred": starred,
        "last_run": dict(last_run) if last_run else None,
    }


# ---------- runs ----------
def start_run():
    with conn() as c:
        return c.execute("INSERT INTO runs(started_at, status, log, new_count) VALUES (?,?,?,0)",
                         (now(), "running", "")).lastrowid


def finish_run(run_id, status, log, new_count):
    with conn() as c:
        c.execute("UPDATE runs SET finished_at=?, status=?, log=?, new_count=? WHERE id=?",
                  (now(), status, log, new_count, run_id))


def ran_since(iso_ts):
    """iso_ts 이후 시작된 수집이 있는지 (실패 포함 - 실패 시 무한 재시도 방지)"""
    with conn() as c:
        return c.execute("SELECT 1 FROM runs WHERE started_at>=? LIMIT 1", (iso_ts,)).fetchone() is not None


# ---------- briefings ----------
def save_briefing(day, content, engine):
    with conn() as c:
        c.execute("INSERT OR REPLACE INTO briefings(day, content, engine, created_at) VALUES (?,?,?,?)",
                  (day, content, engine, now()))


def latest_briefing():
    with conn() as c:
        r = c.execute("SELECT * FROM briefings ORDER BY day DESC LIMIT 1").fetchone()
        return dict(r) if r else None


# ---------- papers ----------
def create_paper(filename, stored_path, source_item=None):
    with conn() as c:
        return c.execute(
            "INSERT INTO papers(filename, stored_path, title, status, message, created_at, source_item)"
            " VALUES (?,?,?,?,?,?,?)",
            (filename, stored_path, filename, "queued", "대기 중", now(), source_item)).lastrowid


def update_paper(pid, **fields):
    if "analysis" in fields and not isinstance(fields["analysis"], str):
        fields["analysis"] = json.dumps(fields["analysis"], ensure_ascii=False)
    with conn() as c:
        sets = ", ".join(f"{k}=?" for k in fields)
        c.execute(f"UPDATE papers SET {sets} WHERE id=?", (*fields.values(), pid))


def list_papers():
    with conn() as c:
        rows = c.execute("SELECT id, filename, title, status, message, engine, created_at, report_path "
                         "FROM papers ORDER BY id DESC").fetchall()
        return [dict(r) for r in rows]


def get_paper(pid):
    with conn() as c:
        r = c.execute("SELECT * FROM papers WHERE id=?", (pid,)).fetchone()
        return _row(r) if r else None


def delete_paper(pid):
    p = get_paper(pid)
    if not p:
        return
    for path in (p.get("stored_path"), p.get("report_path")):
        if path and os.path.exists(path):
            try:
                os.remove(path)
            except OSError:
                pass
    with conn() as c:
        c.execute("DELETE FROM papers WHERE id=?", (pid,))
