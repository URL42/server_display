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

        raw_tasks = await todoist.get_tasks_for_section(sec_id)

        # XP-weighted look-ahead totals (remaining tasks; state.py adds back
        # already-earned XP mid-day so the denominator stays stable)
        today_avail = todoist.xp_available_today(raw_tasks)
        week_avail  = todoist.xp_available_this_week(raw_tasks)

        tasks_out = []
        for t in raw_tasks:
            if not todoist.is_today_or_overdue(t):
                continue
            tasks_out.append(Task(
                id      = t["id"],
                title   = t["content"],
                done    = False,
                overdue = todoist.is_overdue(t),
                xp      = todoist.task_weight(t["content"])
            ))

        s  = await state.reset_if_needed(name, today_avail, week_avail)
        xp = state.compute_xp(s)

        member = Member(name=name, tasks=tasks_out, xp=XP(**xp))
        if name == "Yun":
            ys = state.yun_stats(s)
            member.level              = ys["level"]
            member.level_progress_pct = ys["level_progress_pct"]
            member.streak             = ys["streak"]
            member.freezes            = ys["freezes"]

        members.append(member)

    return ChoresPayload(date=today, members=members)


@router.post("/complete", response_model=CompleteResponse)
async def complete_chore(req: CompleteRequest):
    try:
        # Look up the task BEFORE closing it — we need the title for weighting
        task  = await todoist.get_task(req.task_id)
        title = task["content"] if task else "unknown"
        base  = todoist.task_weight(title)

        ok = await todoist.close_task(req.task_id)
        if not ok:
            return CompleteResponse(ok=False, error="Todoist close failed")

        earned = await state.record_completion(req.member, req.task_id, title, base)
        return CompleteResponse(ok=True, xp_earned=earned)
    except Exception as e:
        return CompleteResponse(ok=False, error=str(e))
