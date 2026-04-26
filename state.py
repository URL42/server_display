import aiosqlite
import datetime
import json
from zoneinfo import ZoneInfo
from config import settings

DB = settings.db_path
TZ = ZoneInfo("America/Los_Angeles")

async def init_db():
    async with aiosqlite.connect(DB) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS xp_state (
                member          TEXT PRIMARY KEY,
                daily_done      INTEGER DEFAULT 0,
                daily_total     INTEGER DEFAULT 0,
                weekly_done     INTEGER DEFAULT 0,
                weekly_total    INTEGER DEFAULT 0,
                last_reset_date TEXT DEFAULT '',
                week_start      TEXT DEFAULT '',
                level           INTEGER DEFAULT 1,
                weekly_history  TEXT DEFAULT '[]'
            )
        """)
        await db.commit()
    print("DB initialised:", DB)

def _today() -> str:
    return datetime.datetime.now(TZ).date().isoformat()

def _week_start() -> str:
    today = datetime.datetime.now(TZ).date()
    return (today - datetime.timedelta(days=today.weekday())).isoformat()

def _pct(done: int, total: int) -> int:
    return round((done / total) * 100) if total > 0 else 0

def _level(history: list[int]) -> int:
    avg = sum(history[-4:]) / max(len(history[-4:]), 1)
    if avg >= 90: return 5
    if avg >= 75: return 4
    if avg >= 60: return 3
    if avg >= 40: return 2
    return 1

async def get_state(member: str) -> dict:
    async with aiosqlite.connect(DB) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM xp_state WHERE member = ?", (member,)
        ) as cur:
            row = await cur.fetchone()
    if row is None:
        return {
            "member": member, "daily_done": 0, "daily_total": 0,
            "weekly_done": 0, "weekly_total": 0,
            "last_reset_date": "", "week_start": "", "level": 1,
            "weekly_history": []
        }
    d = dict(row)
    d["weekly_history"] = json.loads(d["weekly_history"])
    return d

async def _upsert(db, s: dict):
    await db.execute("""
        INSERT INTO xp_state
            (member, daily_done, daily_total, weekly_done, weekly_total,
             last_reset_date, week_start, level, weekly_history)
        VALUES (?,?,?,?,?,?,?,?,?)
        ON CONFLICT(member) DO UPDATE SET
            daily_done=excluded.daily_done,
            daily_total=excluded.daily_total,
            weekly_done=excluded.weekly_done,
            weekly_total=excluded.weekly_total,
            last_reset_date=excluded.last_reset_date,
            week_start=excluded.week_start,
            level=excluded.level,
            weekly_history=excluded.weekly_history
    """, (
        s["member"], s["daily_done"], s["daily_total"],
        s["weekly_done"], s["weekly_total"],
        s["last_reset_date"], s["week_start"],
        s["level"], json.dumps(s["weekly_history"])
    ))

async def reset_if_needed(member: str, today_total: int) -> dict:
    """Reset daily/weekly counters if the date has rolled over."""
    s = await get_state(member)
    today = _today()
    ws    = _week_start()
    changed = False

    if s["last_reset_date"] != today:
        # End-of-day: push yesterday's weekly pct into history if week not reset
        if s["weekly_total"] > 0:
            pct = _pct(s["weekly_done"], s["weekly_total"])
            # Only push on actual week boundary to keep weekly history meaningful
        s["daily_done"]      = 0
        s["daily_total"]     = today_total
        s["last_reset_date"] = today
        changed = True

    if s["week_start"] != ws:
        pct = _pct(s["weekly_done"], s["weekly_total"])
        hist = s["weekly_history"] + [pct]
        s["weekly_history"] = hist[-8:]   # keep 8 weeks
        s["weekly_done"]    = 0
        s["weekly_total"]   = 0
        s["week_start"]     = ws
        if s["member"] == "Yun":
            s["level"] = _level(s["weekly_history"])
        changed = True

    # Always refresh today_total so new tasks added mid-day are counted
    s["daily_total"] = today_total + s["daily_done"]

    if changed:
        async with aiosqlite.connect(DB) as db:
            await _upsert(db, s)
            await db.commit()

    return s

async def record_completion(member: str) -> dict:
    """Increment done counters after a task is completed."""
    s = await get_state(member)
    s["daily_done"]  = s.get("daily_done", 0) + 1
    s["weekly_done"] = s.get("weekly_done", 0) + 1
    if member == "Yun":
        wp = _pct(s["weekly_done"], max(s["weekly_total"], 1))
        hist = s["weekly_history"] + [wp]
        s["weekly_history"] = hist[-8:]
        s["level"] = _level(s["weekly_history"])
    async with aiosqlite.connect(DB) as db:
        await _upsert(db, s)
        await db.commit()
    return s

def compute_xp(s: dict) -> dict:
    return {
        "daily_pct":  _pct(s["daily_done"],  max(s["daily_total"],  1)),
        "weekly_pct": _pct(s["weekly_done"], max(s["weekly_total"], 1)),
    }
