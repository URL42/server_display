from pydantic import BaseModel
from typing import List, Optional

class Task(BaseModel):
    id: str
    title: str
    done: bool = False
    overdue: bool = False
    xp: int = 10   # XP value shown next to task on display

class XP(BaseModel):
    daily_pct: int
    weekly_pct: int
    daily_xp: int = 0
    daily_available: int = 0
    weekly_xp: int = 0
    weekly_available: int = 0

class Member(BaseModel):
    name: str
    tasks: List[Task]
    xp: XP
    # Yun-only gamification fields (None for adults)
    level: Optional[int] = None
    level_progress_pct: Optional[int] = None
    streak: Optional[int] = None
    freezes: Optional[int] = None

class ChoresPayload(BaseModel):
    date: str
    members: List[Member]

class CompleteRequest(BaseModel):
    task_id: str
    member: str

class CompleteResponse(BaseModel):
    ok: bool
    error: Optional[str] = None
    xp_earned: int = 0
