"""
XP economy state management.

Design principles (see README for full rationale):
  1. XP is effort-weighted, not task-counted — closes the "farm the 30-second
     chores" exploit (Goodhart's law).
  2. Diminishing returns on repeat completions of the same task in one day —
     2nd completion 50%, 3rd+ 0%. Belt-and-suspenders anti-farming.
  3. Streaks with freeze tokens — loss aversion drives consistency, freezes
     prevent one bad day from destroying motivation (the Duolingo mechanic).
  4. Levels are a cumulative XP bank with gentle decay — "80% to Level 4" is
     motivating; a bad week costs 10% of progress toward the next level,
     never a full-level cliff.
"""

import aiosqlite
import datetime
import json
from zoneinfo import ZoneInfo
from config import settings

DB = settings.db_path
TZ = ZoneInfo("America/Los_Angeles")

# ── Level thresholds (cumulative lifetime XP) ────────────────────────────────
# Widening gaps: early levels come fast (hook), later ones require sustained
# effort (retention). L5 is ~3 months of solid completion for Yun's task load.
LEVEL_THRESHOLDS = [0, 500, 1200, 2200, 3500]   # L1..L5
MAX_LEVEL = 5

# Streak: a day "counts" if daily XP >= this fraction of available XP
STREAK_THRESHOLD = 0.70
# Weekly decay: if weekly XP < this fraction of available, decay fires
DECAY_THRESHOLD = 0.50
# Decay amount: fraction of the XP gap to the next level lost per bad week
DECAY_RATE = 0.10
# Freeze tokens: earned per 7-day streak, max bankable
FREEZE_MAX = 2

# Diminishing returns multipliers by same-day completion count of same task
DIMINISH = {1: 1.0, 2: 0.5}   # 3rd+ = 0


def _today() -> str:
    return datetime.datetime.now(TZ).date().isoformat()

def _week_start() -> str:
    today = datetime.datetime.now(TZ).date()
    return (today - datetime.timedelta(days=today.weekday())).isoformat()

def _pct(part: int, whole: int) -> int:
    return round((part / whole) * 100) if whole > 0 else 0

def _level_for_xp(total_xp: int) -> int:
    lvl = 1
    for i, threshold in enumerate(LEVEL_THRESHOLDS):
        if total_xp >= threshold:
            lvl = i + 1
    return min(lvl, MAX_LEVEL)

def _level_progress_pct(total_xp: int) -> int:
    """% progress from current level threshold to the next."""
    lvl = _level_for_xp(total_xp)
    if lvl >= MAX_LEVEL:
        return 100
    lo = LEVEL_THRESHOLDS[lvl - 1]
    hi = LEVEL_THRESHOLDS[lvl]
    return _pct(total_xp - lo, hi - lo)


async def init_db():
    async with aiosqlite.connect(DB) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS xp_state (
                member           TEXT PRIMARY KEY,
                daily_xp         INTEGER DEFAULT 0,
                daily_available  INTEGER DEFAULT 0,
                weekly_xp        INTEGER DEFAULT 0,
                weekly_available INTEGER DEFAULT 0,
                last_reset_date  TEXT DEFAULT '',
                week_start       TEXT DEFAULT '',
                total_xp         INTEGER DEFAULT 0,
                streak_days      INTEGER DEFAULT 0,
                freeze_tokens    INTEGER DEFAULT 0
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS xp_ledger (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                member       TEXT,
                task_id      TEXT,
                task_title   TEXT,
                xp           INTEGER,
                completed_on TEXT
            )
        """)
        await db.commit()
    print("DB initialised:", DB)


async def get_state(member: str) -> dict:
    async with aiosqlite.connect(DB) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM xp_state WHERE member = ?", (member,)
        ) as cur:
            row = await cur.fetchone()
    if row is None:
        return {
            "member": member,
            "daily_xp": 0, "daily_available": 0,
            "weekly_xp": 0, "weekly_available": 0,
            "last_reset_date": "", "week_start": "",
            "total_xp": 0, "streak_days": 0, "freeze_tokens": 0
        }
    return dict(row)


async def _upsert(db, s: dict):
    await db.execute("""
        INSERT INTO xp_state
            (member, daily_xp, daily_available, weekly_xp, weekly_available,
             last_reset_date, week_start, total_xp, streak_days, freeze_tokens)
        VALUES (?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(member) DO UPDATE SET
            daily_xp=excluded.daily_xp,
            daily_available=excluded.daily_available,
            weekly_xp=excluded.weekly_xp,
            weekly_available=excluded.weekly_available,
            last_reset_date=excluded.last_reset_date,
            week_start=excluded.week_start,
            total_xp=excluded.total_xp,
            streak_days=excluded.streak_days,
            freeze_tokens=excluded.freeze_tokens
    """, (
        s["member"], s["daily_xp"], s["daily_available"],
        s["weekly_xp"], s["weekly_available"],
        s["last_reset_date"], s["week_start"],
        s["total_xp"], s["streak_days"], s["freeze_tokens"]
    ))


async def _completions_today(db, member: str, title: str) -> int:
    """How many times this member has completed this task title today."""
    async with db.execute(
        "SELECT COUNT(*) FROM xp_ledger WHERE member=? AND task_title=? AND completed_on=?",
        (member, title, _today())
    ) as cur:
        row = await cur.fetchone()
    return row[0] if row else 0


async def reset_if_needed(member: str, today_available: int, week_available: int) -> dict:
    """
    Handle day/week boundary transitions. Called on every /chores poll.

    Daily boundary (Yun only):
      - Evaluate yesterday's streak: daily_xp >= 70% of available → streak +1
        and every 7th consecutive day banks a freeze token (max 2).
      - Below 70%: auto-spend a freeze if banked, else streak resets to 0.

    Weekly boundary (Yun only):
      - If weekly XP < 50% of available, decay total_xp by 10% of the gap
        to the next level. Gradual slide, never a cliff.
    """
    s = await get_state(member)
    today = _today()
    ws    = _week_start()
    changed = False

    # ── Weekly boundary ──
    if s["week_start"] != ws:
        if member == "Yun" and s["week_start"]:   # skip very first run
            if s["weekly_available"] > 0 and \
               s["weekly_xp"] < s["weekly_available"] * DECAY_THRESHOLD:
                lvl = _level_for_xp(s["total_xp"])
                if lvl < MAX_LEVEL:
                    gap = LEVEL_THRESHOLDS[lvl] - LEVEL_THRESHOLDS[lvl - 1]
                else:
                    gap = LEVEL_THRESHOLDS[-1] - LEVEL_THRESHOLDS[-2]
                s["total_xp"] = max(0, s["total_xp"] - int(gap * DECAY_RATE))
                print(f"Decay applied to {member}: -{int(gap * DECAY_RATE)} XP")
        s["weekly_xp"]        = 0
        s["weekly_available"] = week_available
        s["week_start"]       = ws
        changed = True

    # ── Daily boundary ──
    if s["last_reset_date"] != today:
        if member == "Yun" and s["last_reset_date"]:   # skip very first run
            hit = s["daily_available"] > 0 and \
                  s["daily_xp"] >= s["daily_available"] * STREAK_THRESHOLD
            if hit:
                s["streak_days"] += 1
                if s["streak_days"] % 7 == 0 and s["freeze_tokens"] < FREEZE_MAX:
                    s["freeze_tokens"] += 1
                    print(f"{member} earned a freeze token (streak {s['streak_days']})")
            else:
                if s["freeze_tokens"] > 0:
                    s["freeze_tokens"] -= 1
                    print(f"{member} spent a freeze token — streak protected")
                else:
                    s["streak_days"] = 0
        s["daily_xp"]        = 0
        s["daily_available"] = today_available
        s["last_reset_date"] = today
        changed = True
    else:
        # Mid-day: available = remaining tasks' XP + what's already earned.
        # Keeps the denominator stable as tasks complete and disappear.
        new_avail = today_available + s["daily_xp"]
        new_wk    = week_available + s["weekly_xp"]
        if new_avail != s["daily_available"] or new_wk != s["weekly_available"]:
            s["daily_available"]  = max(s["daily_available"], new_avail) \
                if s["daily_available"] else new_avail
            s["weekly_available"] = max(s["weekly_available"], new_wk) \
                if s["weekly_available"] else new_wk
            changed = True

    if changed:
        async with aiosqlite.connect(DB) as db:
            await _upsert(db, s)
            await db.commit()

    return s


async def record_completion(member: str, task_id: str, title: str, base_xp: int) -> int:
    """
    Log a completion, apply diminishing returns, credit XP.
    Returns the XP actually earned.
    """
    async with aiosqlite.connect(DB) as db:
        prior = await _completions_today(db, member, title)
        multiplier = DIMINISH.get(prior + 1, 0.0)
        earned = int(base_xp * multiplier)

        await db.execute(
            "INSERT INTO xp_ledger (member, task_id, task_title, xp, completed_on) "
            "VALUES (?,?,?,?,?)",
            (member, task_id, title, earned, _today())
        )

        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM xp_state WHERE member=?", (member,)
        ) as cur:
            row = await cur.fetchone()
        s = dict(row) if row else await get_state(member)

        s["daily_xp"]  = s.get("daily_xp", 0) + earned
        s["weekly_xp"] = s.get("weekly_xp", 0) + earned
        if member == "Yun":
            s["total_xp"] = s.get("total_xp", 0) + earned

        await _upsert(db, s)
        await db.commit()

    return earned


def compute_xp(s: dict) -> dict:
    return {
        "daily_pct":  _pct(s["daily_xp"], max(s["daily_available"], 1)),
        "weekly_pct": _pct(s["weekly_xp"], max(s["weekly_available"], 1)),
        "daily_xp":         s["daily_xp"],
        "daily_available":  s["daily_available"],
        "weekly_xp":        s["weekly_xp"],
        "weekly_available": s["weekly_available"],
    }


def yun_stats(s: dict) -> dict:
    return {
        "level":              _level_for_xp(s["total_xp"]),
        "level_progress_pct": _level_progress_pct(s["total_xp"]),
        "streak":             s["streak_days"],
        "freezes":            s["freeze_tokens"],
    }
