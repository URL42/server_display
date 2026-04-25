import datetime
import httpx
from config import settings

BASE    = "https://api.todoist.com/api/v1"
HEADERS = {"Authorization": f"Bearer {settings.todoist_api_token}"}

SECTION_NAMES = ["Mama", "Baba", "Yun"]

async def get_sections() -> dict[str, str]:
    """Returns {section_name: section_id} for the chore project."""
    async with httpx.AsyncClient() as client:
        r = await client.get(
            f"{BASE}/sections",
            headers=HEADERS,
            params={"project_id": settings.todoist_project_id}
        )
        r.raise_for_status()
    return {s["name"]: s["id"] for s in r.json()}

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
    return r.json()

async def close_task(task_id: str) -> bool:
    """Marks a task complete. Returns True on success."""
    async with httpx.AsyncClient() as client:
        r = await client.post(
            f"{BASE}/tasks/{task_id}/close",
            headers=HEADERS
        )
    return r.status_code == 204

def is_overdue(task: dict) -> bool:
    due = task.get("due")
    if not due:
        return False
    due_date = due.get("date", "")[:10]
    return bool(due_date) and due_date < datetime.date.today().isoformat()

def is_today_or_overdue(task: dict) -> bool:
    due = task.get("due")
    if not due:
        return False
    due_date = due.get("date", "")[:10]
    return bool(due_date) and due_date <= datetime.date.today().isoformat()
