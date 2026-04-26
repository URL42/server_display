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


def _compute_level(history: list[int]) -> int:
    """
    Level based on rolling 4-week average of weekly completion %.
    Only updated at week boundaries — never mid-week.
    Increments and decrements by 1 at a time so it builds/falls gradually.
    """
    if not history:
        return 1
    avg = sum(history[-4:]) / min(len(history), 4)
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
            "last_reset_date": "", "week_start": "",
            "level": 1, "weekly_history": []
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


async def reset_if_needed(
    member: str,
    today_total: int,
    week_total: int
) -> dict:
    """
    Reset daily/weekly counters at day/week boundaries.

    - daily_total  = tasks due today (looked up from Todoist, passed in)
    - weekly_total = tasks due Mon-Sun this week (looked up, passed in)
    - Both are set fresh at reset time, NOT accumulated during the day
    - daily_done / weekly_done only increment via record_completion()
    - Yun's level only updates at week reset, never mid-week
    """
    s = await get_state(member)
    today = _today()
    ws    = _week_start()
    changed = False

    # ── Weekly reset (Monday) ─────────────────────────────────────────────
    if s["week_start"] != ws:
        # Snapshot the completed week's % into history
        final_pct = _pct(s["weekly_done"], max(s["weekly_total"], 1))
        hist = s["weekly_history"] + [final_pct]
        hist = hist[-8:]  # keep 8 weeks

        s["weekly_history"] = hist
        s["weekly_done"]    = 0
        s["weekly_total"]   = week_total
        s["week_start"]     = ws

        # Yun's level: only move by 1 step per week — gradual up/down
        if member == "Yun":
            target = _compute_level(hist)
            current = s["level"]
            if target > current:
                s["level"] = current + 1      # step up one level
            elif target < current:
                s["level"] = current - 1      # step down one level
            # else: stay the same
            s["level"] = max(1, min(5, s["level"]))  # clamp 1-5

        changed = True

    # ── Daily reset ────────────────────────────────────────────────────────
    if s["last_reset_date"] != today:
        s["daily_done"]      = 0
        s["daily_total"]     = today_total
        s["last_reset_date"] = today
        changed = True
    else:
        # Mid-day: refresh total in case tasks were added (but don't reset done)
        # Only update if the new total is >= what we've already done
        new_total = max(today_total, s["daily_done"])
        if new_total != s["daily_total"]:
            s["daily_total"] = new_total
            changed = True

    if changed:
        async with aiosqlite.connect(DB) as db:
            await _upsert(db, s)
            await db.commit()

    return s


async def record_completion(member: str) -> dict:
    """
    Increment done counters when a task is completed.
    Does NOT update Yun's level — that only happens at week reset.
    """
    s = await get_state(member)
    s["daily_done"]  = min(s.get("daily_done", 0) + 1, s.get("daily_total", 99))
    s["weekly_done"] = min(s.get("weekly_done", 0) + 1, s.get("weekly_total", 99))
    async with aiosqlite.connect(DB) as db:
        await _upsert(db, s)
        await db.commit()
    return s


def compute_xp(s: dict) -> dict:
    return {
        "daily_pct":  _pct(s["daily_done"], max(s["daily_total"], 1)),
        "weekly_pct": _pct(s["weekly_done"], max(s["weekly_total"], 1)),
    }
