import asyncio
import mimetypes
import logging
import secrets
import time
import uuid
import hashlib
from filelock import FileLock
from sqlalchemy.engine import make_url
from sqlalchemy import or_
from fastapi.responses import JSONResponse
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pathlib import Path
from sqlalchemy import select, func
from sqlalchemy.exc import OperationalError, TimeoutError as PoolTimeoutError
from app.api.routes import router
from app.core.config import get_settings
from app.db.base import engine, SessionLocal
from app.models.entities import Base, Customer, Campaign, ExecutionLease
from app.services.campaigns import generate_candidates

logging.basicConfig(level=logging.INFO)


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.tasks = set()
    app.state.local_probe_lock = asyncio.Lock()
    app.state.admission_lock = asyncio.Lock()
    app.state.pending_admissions = 0
    app.state.model_slots = asyncio.Semaphore(get_settings().model_concurrency)

    def track(task):
        app.state.tasks.add(task)
        def finished(done):
            app.state.tasks.discard(done)
            if not done.cancelled() and done.exception() is not None:
                logging.getLogger(__name__).error("Background task failed: %s", type(done.exception()).__name__)
        task.add_done_callback(finished)
    app.state.track_task = track
    url = make_url(get_settings().database_url)
    lock_path = (str(Path(url.database).resolve()) + ".init.lock" if url.drivername.startswith("sqlite")
                 else str(Path(get_settings().artifact_dir).resolve() / (hashlib.sha256(str(url).encode()).hexdigest()+".init.lock")))
    Path(lock_path).parent.mkdir(parents=True,exist_ok=True)
    init_lock = FileLock(lock_path, timeout=20, thread_local=False)
    await asyncio.to_thread(init_lock.acquire)
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with SessionLocal() as db:
            count = await db.scalar(select(func.count()).select_from(Customer))
            if not count:
                db.add_all([Customer(id=i, segment=("new", "active", "dormant")[(i - 1) % 3],
                                     region=("north", "south", "east", "west")[(i - 1) % 4],
                                     consent=(i % 9 != 0)) for i in range(1, 501)])
                await db.commit()
            from app.services.retail import seed_retail
            await seed_retail(db)
    finally:
        await asyncio.to_thread(init_lock.release)

    async def recover_once():
        capacity = get_settings().max_active_campaigns - len(app.state.tasks) - app.state.pending_admissions
        if capacity <= 0:return
        async with SessionLocal() as db:
            pending = (await db.scalars(select(Campaign.id).outerjoin(ExecutionLease,
                Campaign.id == ExecutionLease.campaign_id).where(Campaign.status == "GENERATING",
                or_(ExecutionLease.campaign_id.is_(None), ExecutionLease.expires_at <= time.time()))
                .order_by(Campaign.created_at).limit(capacity))).all()
        async with app.state.admission_lock:
            available = get_settings().max_active_campaigns - len(app.state.tasks) - app.state.pending_admissions
            for cid in pending[:max(0, available)]:
                app.state.track_task(asyncio.create_task(generate_candidates(cid, app.state.model_slots)))

    async def recovery_loop():
        while True:
            await asyncio.sleep(get_settings().recovery_poll_seconds)
            try:await recover_once()
            except Exception:logging.getLogger(__name__).exception("Recovery scan failed")

    await recover_once()
    recovery_task = asyncio.create_task(recovery_loop())
    try:
        yield
    finally:
        recovery_task.cancel()
        await asyncio.gather(recovery_task, return_exceptions=True)
        owned = list(app.state.tasks)
        if owned:
            _, pending = await asyncio.wait(owned, timeout=get_settings().shutdown_grace_seconds)
            for task in pending:
                task.cancel()
            await asyncio.gather(*owned, return_exceptions=True)
        await engine.dispose()


app = FastAPI(title="FlowPilot API", version="0.1.0", lifespan=lifespan,
              description="Campaign planning, candidate approval, audience export, experiments and support confirmation. Uses synthetic customers; no live delivery or refunds.")
app.add_middleware(CORSMiddleware, allow_origins=[o.strip() for o in get_settings().cors_origins.split(",") if o.strip()],
                   allow_credentials=True, allow_methods=["*"], allow_headers=["*"])
from app.api.experiments import router as experiment_router
@app.middleware("http")
async def request_context(request, call_next):
    started = time.perf_counter()
    request_id = uuid.uuid4().hex
    token = get_settings().access_token
    if token and request.url.path.startswith("/api/") and request.url.path != "/api/v1/health":
        if not secrets.compare_digest(request.headers.get("authorization", "").encode("utf-8"), ("Bearer " + token).encode("utf-8")):
            return JSONResponse({"detail": "Unauthorized"}, status_code=401,
                                headers={"X-Request-ID": request_id})
    response = await call_next(request)
    response.headers["X-Request-ID"] = request_id
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "same-origin"
    if request.url.path.startswith("/api/v1/local-model"):
        response.headers["Cache-Control"] = "no-store"
    response.headers["Server-Timing"] = f"app;dur={(time.perf_counter()-started)*1000:.2f}"
    logging.getLogger("access").info("request_id=%s method=%s path=%s status=%s", request_id,
                                   request.method, request.url.path, response.status_code)
    return response

@app.exception_handler(OperationalError)
@app.exception_handler(PoolTimeoutError)
async def database_busy(request, exc):
    return JSONResponse({"detail":"Database temporarily unavailable"}, status_code=503,
                        headers={"Retry-After":"2"})

from app.core.body_limit import RequestBodyLimit
app.add_middleware(RequestBodyLimit)

app.include_router(router)
app.include_router(experiment_router)
from app.api.local_model import router as local_model_router
app.include_router(local_model_router)
from app.api.knowledge import router as knowledge_router
app.include_router(knowledge_router)
from app.api.operations import router as operations_router
app.include_router(operations_router)
from app.api.retail import router as retail_router
app.include_router(retail_router)

mimetypes.add_type("text/javascript", ".js")
mimetypes.add_type("text/css", ".css")
app.mount("/", StaticFiles(directory=str(Path(__file__).resolve().parent.parent / "web"), html=True), name="demo")
