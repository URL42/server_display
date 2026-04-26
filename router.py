import datetime
from zoneinfo import ZoneInfo
from fastapi import APIRouter
from chores.models import ChoresPayload, CompleteRequest, CompleteResponse, Member, Task, XP
from chores import todoist, state

router = APIRouter()
TZ = ZoneInfo("America/Los_Angeles")


@router.get("", response_model=ChoresPayload)
async def get_chores():
    today    = datetime.datetime.now(TZ).date().isoformat()
    sections = await todoist.get_sections()
    members  = []

    for name in todoist.SECTION_NAMES:
        sec_id = sections.get(name)
        if not sec_id:
            continue

        # Fetch all incomplete tasks for this section
        raw_tasks = await todoist.get_tasks_for_section(sec_id)

        # Look-ahead totals — count what's scheduled, not what's left
        # Since Todoist only returns incomplete tasks, these counts reflect
        # remaining tasks. We use them to set the baseline at reset time only.
        today_total = todoist.count_today(raw_tasks)
        week_total  = todoist.count_this_week(raw_tasks)

        # Build display task list (today + overdue only)
        tasks_out = []
        for t in raw_tasks:
            if not todoist.is_today_or_overdue(t):
                continue
            tasks_out.append(Task(
                id      = t["id"],
                title   = t["content"],
                done    = False,
                overdue = todoist.is_overdue(t)
            ))

        # Update XP state with fresh totals
        s  = await state.reset_if_needed(name, today_total, week_total)
        xp = state.compute_xp(s)

        members.append(Member(
            name  = name,
            tasks = tasks_out,
            xp    = XP(**xp),
            level = s["level"] if name == "Yun" else None
        ))

    return ChoresPayload(date=today, members=members)


@router.post("/complete", response_model=CompleteResponse)
async def complete_chore(req: CompleteRequest):
    try:
        ok = await todoist.close_task(req.task_id)
        if not ok:
            return CompleteResponse(ok=False, error="Todoist close failed")
        await state.record_completion(req.member)
        return CompleteResponse(ok=True)
    except Exception as e:
        return CompleteResponse(ok=False, error=str(e))
