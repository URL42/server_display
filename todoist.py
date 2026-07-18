import datetime
import httpx
from zoneinfo import ZoneInfo
from config import settings

BASE    = "https://api.todoist.com/api/v1"
HEADERS = {"Authorization": f"Bearer {settings.todoist_api_token}"}

SECTION_NAMES = ["Mama", "Baba", "Yun"]
TZ = ZoneInfo("America/Los_Angeles")

# ── XP weights ────────────────────────────────────────────────────────────────
# Effort-based task values. Matched by keyword against the task title
# (case-insensitive). First match wins, top to bottom. Default is 10.
#
# Design rationale:
#   - Equal-value tasks let a rational kid farm the 30-second chores and skip
#     the 20-minute ones while keeping a high completion %. Weighting by
#     effort aligns the metric with what we actually want: effort.
WEIGHT_TABLE = [
    (40, ("clean refrigerator", "clean stove", "clean freezer", "clean car")),
    (25, ("clean room", "changing sheets", "clean bedroom", "help yun")),
    (15, ("laundry", "vacuum", "poop", "bathtub")),
    (5,  ("pillow", "school sheet", "bathroom trash", "water bottle")),
]
DEFAULT_WEIGHT = 10

def task_weight(title: str) -> int:
    t = title.lower()
    for xp, keywords in WEIGHT_TABLE:
        if any(k in t for k in keywords):
            return xp
    return DEFAULT_WEIGHT


# ── Date helpers ──────────────────────────────────────────────────────────────

def _today() -> str:
    return datetime.datetime.now(TZ).date().isoformat()

def _due_date_local(task: dict) -> str:
    """
    Due date in Pacific time.
    Fixed-time tasks: UTC ISO string e.g. 2026-04-28T14:00:00Z
    All-day tasks: plain date string e.g. 2026-04-28
    """
    due = task.get("due")
    if not due:
        return ""
    date_str = due.get("date", "")
    if not date_str:
        return ""
    if len(date_str) == 10:
        return date_str
    try:
        dt_utc = datetime.datetime.fromisoformat(date_str.replace("Z", "+00:00"))
        return dt_utc.astimezone(TZ).date().isoformat()
    except Exception:
        return date_str[:10]

def is_overdue(task: dict) -> bool:
    due_date = _due_date_local(task)
    return bool(due_date) and due_date < _today()

def is_today_or_overdue(task: dict) -> bool:
    due_date = _due_date_local(task)
    return bool(due_date) and due_date <= _today()

def is_this_week(task: dict) -> bool:
    due_date = _due_date_local(task)
    if not due_date:
        return False
    today = datetime.datetime.now(TZ).date()
    week_start = today - datetime.timedelta(days=today.weekday())
    week_end   = week_start + datetime.timedelta(days=6)
    d = datetime.date.fromisoformat(due_date)
    return week_start <= d <= week_end


# ── API calls ─────────────────────────────────────────────────────────────────

async def get_sections() -> dict[str, str]:
    async with httpx.AsyncClient() as client:
        r = await client.get(
            f"{BASE}/sections",
            headers=HEADERS,
            params={"project_id": settings.todoist_project_id}
        )
        r.raise_for_status()
    return {s["name"]: s["id"] for s in r.json()["results"]}

async def get_tasks_for_section(section_id: str) -> list[dict]:
    async with httpx.AsyncClient() as client:
        r = await client.get(
            f"{BASE}/tasks",
            headers=HEADERS,
            params={
                "project_id": settings.todoist_project_id,
                "section_id": section_id
            }
        )
        r.raise_for_status()
    data = r.json()
    return data["results"] if "results" in data else data

async def get_task(task_id: str) -> dict | None:
    """Fetch a single task by ID (used to get title/weight before closing)."""
    async with httpx.AsyncClient() as client:
        r = await client.get(f"{BASE}/tasks/{task_id}", headers=HEADERS)
    if r.status_code != 200:
        return None
    return r.json()

async def close_task(task_id: str) -> bool:
    async with httpx.AsyncClient() as client:
        r = await client.post(f"{BASE}/tasks/{task_id}/close", headers=HEADERS)
    return r.status_code == 204


# ── XP counting ───────────────────────────────────────────────────────────────

def xp_available_today(tasks: list[dict]) -> int:
    """Sum of XP for tasks due today or overdue."""
    return sum(task_weight(t["content"]) for t in tasks if is_today_or_overdue(t))

def xp_available_this_week(tasks: list[dict]) -> int:
    """Sum of XP for tasks due Mon-Sun this Pacific week."""
    return sum(task_weight(t["content"]) for t in tasks if is_this_week(t))
