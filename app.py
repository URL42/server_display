from fastapi import FastAPI
from contextlib import asynccontextmanager
from chores.router import router as chores_router
from chores.state import init_db

@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    yield

app = FastAPI(title="server-display", lifespan=lifespan)
app.include_router(chores_router, prefix="/chores", tags=["chores"])

@app.get("/health")
async def health():
    return {"ok": True}
