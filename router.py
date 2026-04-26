import datetime
from zoneinfo import ZoneInfo
from fastapi import APIRouter, HTTPException
from chores.models import ChoresPayload, CompleteRequest, CompleteResponse, Member, Task, XP
from chores import todoist, state

router = APIRouter()
TZ = ZoneInfo("America/Los_Angeles")

@router.get("", response_model=ChoresPayload)
async def get_chores():
    today = datetime.datetime.now(TZ).date().isoformat()
    sections  = await todoist.get_sections()
    members   = []

    for name in todoist.SECTION_NAMES:
        sec_id = sections.get(name)
        if not sec_id:
            continue

        raw_tasks  = await todoist.get_tasks_for_section(sec_id)
        tasks_out  = []
        today_total = 0

        for t in raw_tasks:
            if not todoist.is_today_or_overdue(t):
                continue
            today_total += 1
            tasks_out.append(Task(
                id      = t["id"],
                title   = t["content"],
                done    = False,          # Todoist REST only returns incomplete
                overdue = todoist.is_overdue(t)
            ))

        s   = await state.reset_if_needed(name, today_total)
        xp  = state.compute_xp(s)

        member = Member(
            name  = name,
            tasks = tasks_out,
            xp    = XP(**xp),
            level = s["level"] if name == "Yun" else None
        )
        members.append(member)

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
