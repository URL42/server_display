from pydantic import BaseModel
from typing import List, Optional

class Task(BaseModel):
    id: str
    title: str
    done: bool = False
    overdue: bool = False

class XP(BaseModel):
    daily_pct: int
    weekly_pct: int

class Member(BaseModel):
    name: str
    tasks: List[Task]
    xp: XP
    level: Optional[int] = None   # Yun only

class ChoresPayload(BaseModel):
    date: str
    members: List[Member]

class CompleteRequest(BaseModel):
    task_id: str
    member: str

class CompleteResponse(BaseModel):
    ok: bool
    error: Optional[str] = None
