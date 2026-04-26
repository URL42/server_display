import datetime
import httpx
from zoneinfo import ZoneInfo
from config import settings

BASE    = "https://api.todoist.com/api/v1"
HEADERS = {"Authorization": f"Bearer {settings.todoist_api_token}"}

SECTION_NAMES = ["Mama", "Baba", "Yun"]
TZ = ZoneInfo("America/Los_Angeles")

def _today() -> str:
    """Today's date in Pacific time."""
    return datetime.datetime.now(TZ).date().isoformat()

async def get_sections() -> dict[str, str]:
    """Returns {section_name: section_id} for the chore project."""
    async with httpx.AsyncClient() as client:
        r = await client.get(
            f"{BASE}/sections",
            headers=HEADERS,
            params={"project_id": settings.todoist_project_id}
        )
        r.raise_for_status()
    return {s["name"]: s["id"] for s in r.json()["results"]}

async def get_tasks_for_section(section_id: str) -> list[dict]:
    """Returns all active (incomplete) tasks in a section."""
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

async def close_task(task_id: str) -> bool:
    """Marks a task complete. Returns True on success."""
    async with httpx.AsyncClient() as client:
        r = await client.post(
            f"{BASE}/tasks/{task_id}/close",
            headers=HEADERS
        )
    return r.status_code == 204

def _due_date_local(task: dict) -> str:
    """
    Extract the due date in Pacific time.
    Todoist returns fixed-time tasks as UTC ISO strings (e.g. 2026-04-28T14:00:00Z).
    All-day tasks return plain date strings (e.g. 2026-04-28).
    We convert UTC timestamps to Pacific before extracting the date portion.
    """
    due = task.get("due")
    if not due:
        return ""
    date_str = due.get("date", "")
    if not date_str:
        return ""
    # All-day task — no time component, already local date
    if len(date_str) == 10:
        return date_str
    # Has time component — parse as UTC and convert to Pacific
    try:
        dt_utc = datetime.datetime.fromisoformat(date_str.replace("Z", "+00:00"))
        dt_pacific = dt_utc.astimezone(TZ)
        return dt_pacific.date().isoformat()
    except Exception:
        return date_str[:10]

def is_overdue(task: dict) -> bool:
    due_date = _due_date_local(task)
    return bool(due_date) and due_date < _today()

def is_today_or_overdue(task: dict) -> bool:
    due_date = _due_date_local(task)
    return bool(due_date) and due_date <= _today()
