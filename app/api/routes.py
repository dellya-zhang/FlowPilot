import asyncio
import json
import logging
from contextlib import asynccontextmanager
from fastapi import APIRouter, Depends, HTTPException, Request, Query
from fastapi.responses import StreamingResponse, Response
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from app.db.base import session_dep, SessionLocal
from app.models.entities import Campaign, Candidate, Event, Artifact, SupportSession, Experiment, PlanningContext, CandidatePlan, ArtifactBinding
from app.schemas.contracts import (CampaignCreate, CampaignView, CandidateView, Approval,
                                   ArtifactView, SupportMessageInput, SupportDecision, PlanningInput)
from app.services.campaigns import generate_candidates, export_audience, require_campaign, emit, transition
from app.services.storage import get_verified_blob
from app.services.support import send_message, confirm_action
from app.services.planning import planning_input, missing_inputs, audience_pool, freeze_audience, plan_document
from app.services.strategy_design import variants
from app.services.versions import checked_snapshot, bind_decision
from app.services.limiter import limit
from app.services.support import history, audit_history
from app.core.config import get_settings

router = APIRouter(prefix="/api/v1")


@router.get("/health")
async def health():
    return {"status": "ok"}


@router.post("/campaigns", response_model=CampaignView, status_code=201)
async def create_campaign(data: CampaignCreate, db: AsyncSession = Depends(session_dep)):
    if data.budget > 100_000:
        raise HTTPException(422, "Budget exceeds demo policy; no model call was made")
    values = data.model_dump(exclude={'planning'})
    planning = data.planning.model_dump() if data.planning else None
    campaign = Campaign(**values)
    if planning and missing_inputs(planning):
        campaign.status = 'NEEDS_CLARIFICATION'
    db.add(campaign)
    await db.flush()
    if planning is not None:
        db.add(PlanningContext(campaign_id=campaign.id, data=planning))
    await db.commit()
    await db.refresh(campaign)
    return campaign


@router.get("/campaigns", response_model=list[CampaignView])
async def list_campaigns(db: AsyncSession = Depends(session_dep)):
    return list((await db.scalars(select(Campaign).order_by(Campaign.created_at.desc()).limit(100))).all())


@router.get("/campaigns/{campaign_id}", response_model=CampaignView)
async def get_campaign(campaign_id: str, db: AsyncSession = Depends(session_dep)):
    return await require_campaign(db, campaign_id)


@asynccontextmanager
async def generation_slot(app):
    # Reserve before any DB await: len(tasks) alone races under request bursts.
    async with app.state.admission_lock:
        if len(app.state.tasks) + app.state.pending_admissions >= get_settings().max_active_campaigns:
            raise HTTPException(429, "Campaign capacity reached", headers={"Retry-After":"2"})
        app.state.pending_admissions += 1
    try:
        yield
    finally:
        async with app.state.admission_lock:
            app.state.pending_admissions -= 1


@router.post("/campaigns/{campaign_id}/generate", status_code=202)
async def start_generate(campaign_id: str, request: Request, db: AsyncSession = Depends(session_dep)):
    await limit(request, "generate", 10)
    async with generation_slot(request.app):
        await db.execute(update(Campaign).where(Campaign.id == campaign_id).values(updated_at=Campaign.updated_at))
        campaign = await require_campaign(db, campaign_id)
        if campaign.status != "CREATED":
            raise HTTPException(409, f"Cannot generate in {campaign.status}")
        planning = await planning_input(db, campaign_id)
        if planning:
            audience, _ = await audience_pool(db, campaign)
            if not audience:
                raise HTTPException(422, '没有符合条件且同意触达的用户，请调整人群条件')
            designs=[plan_document({'planning':planning,'brief':campaign.brief,'audience':campaign.audience,'budget':campaign.budget,'variant':v,'audience_count':len(audience)}) for v in variants(planning)]
            if designs and all(p['cost_estimate']['within_budget'] is False for p in designs):
                raise HTTPException(422, '预计触达费用超过活动预算，请调整单价或人群条件；未调用模型')
        await freeze_audience(db,campaign)
        await transition(db, campaign_id, "CREATED", "GENERATING", event_name="generation.queued")
        task = asyncio.create_task(generate_candidates(campaign.id, request.app.state.model_slots))
        request.app.state.track_task(task)
        return {"campaign_id": campaign.id, "status": "GENERATING"}


@router.get("/campaigns/{campaign_id}/planning")
async def get_planning(campaign_id: str, db: AsyncSession = Depends(session_dep)):
    campaign = await require_campaign(db, campaign_id)
    data = await planning_input(db, campaign_id)
    exp = await db.scalar(select(Experiment).where(Experiment.campaign_id == campaign_id))
    return {"input":data, "questions":missing_inputs(data) if data else [],
            "editable":campaign.status in ('CREATED','NEEDS_CLARIFICATION'),
            "experiment_id":exp.id if exp else None}


@router.put("/campaigns/{campaign_id}/planning", response_model=CampaignView)
async def clarify_planning(campaign_id: str, data: PlanningInput, db: AsyncSession = Depends(session_dep)):
    await db.execute(update(Campaign).where(Campaign.id == campaign_id).values(updated_at=Campaign.updated_at))
    campaign = await require_campaign(db, campaign_id)
    if campaign.status not in ('CREATED','NEEDS_CLARIFICATION'):
        raise HTTPException(409, 'Planning input is frozen after generation starts')
    row = await db.get(PlanningContext, campaign_id)
    if row:
        row.data = data.model_dump()
    else:
        db.add(PlanningContext(campaign_id=campaign_id, data=data.model_dump()))
    campaign.status = 'NEEDS_CLARIFICATION' if missing_inputs(data.model_dump()) else 'CREATED'
    await emit(db, campaign_id, 'planning.updated', {'status':campaign.status})
    return campaign


@router.get("/campaigns/{campaign_id}/candidates", response_model=list[CandidateView])
async def candidates(campaign_id: str, db: AsyncSession = Depends(session_dep)):
    await require_campaign(db, campaign_id)
    rows = list((await db.scalars(select(Candidate).where(Candidate.campaign_id == campaign_id)
                                 .order_by(Candidate.variant))).all())
    plans = {p.candidate_id:p.data for p in (await db.scalars(select(CandidatePlan).join(Candidate, Candidate.id == CandidatePlan.candidate_id).where(Candidate.campaign_id == campaign_id))).all()}
    for row in rows:
        row.plan_data = plans.get(row.id)
    return rows


@router.get("/campaigns/{campaign_id}/events")
async def get_events(campaign_id: str, after: int = Query(default=0, ge=0, le=9223372036854775807), db: AsyncSession = Depends(session_dep)):
    await require_campaign(db, campaign_id)
    events = (await db.scalars(select(Event).where(Event.campaign_id == campaign_id, Event.id > after)
                               .order_by(Event.id).limit(200))).all()
    return [{"id": e.id, "name": e.name, "payload": e.payload} for e in events]


@router.get("/campaigns/{campaign_id}/stream")
async def event_stream(campaign_id: str, request: Request, after: int = Query(default=0, ge=0, le=9223372036854775807),
                       db: AsyncSession = Depends(session_dep)):
    await require_campaign(db, campaign_id)
    header = request.headers.get("last-event-id")
    if header:
        if not header.isascii() or not header.isdecimal() or len(header) > 19 or int(header) > 9223372036854775807:
            raise HTTPException(422, "Invalid Last-Event-ID")
        after = max(after, int(header))
    # The SSE response must not retain a DB connection for its lifetime.
    await db.rollback()

    async def events():
        cursor = after
        while not await request.is_disconnected():
            async with SessionLocal() as stream_db:
                rows = (await stream_db.scalars(select(Event).where(Event.campaign_id == campaign_id,
                        Event.id > cursor).order_by(Event.id).limit(100))).all()
            if rows:
                for event in rows:
                    cursor = event.id
                    yield f"id: {event.id}\nevent: {event.name}\ndata: {json.dumps(event.payload, ensure_ascii=False)}\n\n"
            else:
                yield ": heartbeat\n\n"
            await asyncio.sleep(0.6)

    return StreamingResponse(events(), media_type="text/event-stream",
                             headers={"Cache-Control":"no-cache", "X-Accel-Buffering":"no"})


@router.post("/campaigns/{campaign_id}/approve", response_model=CampaignView)
async def approve(campaign_id: str, data: Approval, db: AsyncSession = Depends(session_dep)):
    await db.execute(update(Campaign).where(Campaign.id == campaign_id).values(updated_at=Campaign.updated_at))
    campaign = await require_campaign(db, campaign_id)
    if await db.scalar(select(Experiment.id).where(Experiment.campaign_id == campaign_id)):
        raise HTTPException(409, "Campaign belongs to an experiment; use promote")
    if campaign.status != "WAITING_APPROVAL":
        raise HTTPException(409, f"Cannot approve in {campaign.status}")
    candidate = await db.get(Candidate, data.candidate_id)
    if not candidate or candidate.campaign_id != campaign_id:
        raise HTTPException(422, "Candidate does not belong to campaign")
    snapshot=await checked_snapshot(db,candidate,data.expected_revision)
    await freeze_audience(db,campaign)
    await bind_decision(db,campaign,snapshot,'approval',data.decision_reason)
    await transition(db, campaign_id, "WAITING_APPROVAL", "PILOT_READY", selected_candidate_id=candidate.id,
                     event_name="campaign.approved", event_payload={"candidate_id":candidate.id})
    await db.refresh(campaign)
    return campaign


@router.post("/campaigns/{campaign_id}/pilot", response_model=ArtifactView)
async def pilot(campaign_id: str, db: AsyncSession = Depends(session_dep)):
    await db.execute(update(Campaign).where(Campaign.id == campaign_id).values(updated_at=Campaign.updated_at))
    campaign = await require_campaign(db, campaign_id)
    if campaign.status not in ("PILOT_READY", "PILOT_EXPORTED", "COMPLETED"):
        raise HTTPException(409, "Approve a candidate before pilot export")
    if await db.scalar(select(Experiment.id).where(Experiment.campaign_id == campaign_id)):
        raise HTTPException(409, "Experiment cohorts already define the pilot; no separate pilot export")
    if campaign.status == "COMPLETED" and not await db.scalar(select(Artifact.id).where(Artifact.campaign_id == campaign_id, Artifact.kind == "pilot")):
        raise HTTPException(409, "Cannot create a new pilot after full delivery")
    artifact = await export_audience(db, campaign, "pilot")
    if campaign.status == "PILOT_READY":
        campaign.status = "PILOT_EXPORTED"
    await emit(db, campaign_id, "pilot.exported", {"artifact_id": artifact.id, "rows": artifact.row_count})
    return artifact


@router.post("/campaigns/{campaign_id}/full", response_model=ArtifactView)
async def full_delivery(campaign_id: str, db: AsyncSession = Depends(session_dep)):
    """Ordinary approved plan -> full; experiment winner -> remaining audience."""
    await db.execute(update(Campaign).where(Campaign.id == campaign_id).values(updated_at=Campaign.updated_at))
    campaign = await require_campaign(db, campaign_id)
    experiment = await db.scalar(select(Experiment).where(Experiment.campaign_id == campaign_id))
    if campaign.status not in ("PILOT_READY", "PILOT_EXPORTED", "COMPLETED") or (experiment and experiment.status != "PROMOTED"):
        raise HTTPException(409, "Approve a plan or promote an experiment winner first")
    artifact = await export_audience(db, campaign, "full")
    campaign.status = "COMPLETED"
    await emit(db, campaign_id, "campaign.completed", {"artifact_id": artifact.id, "rows": artifact.row_count})
    return artifact


@router.post("/campaigns/{campaign_id}/finalize", response_model=ArtifactView)
async def finalize(campaign_id: str, db: AsyncSession = Depends(session_dep)):
    await db.execute(update(Campaign).where(Campaign.id == campaign_id).values(updated_at=Campaign.updated_at))
    campaign = await require_campaign(db, campaign_id)
    if campaign.status not in ("PILOT_EXPORTED", "COMPLETED"):
        raise HTTPException(409, "Pilot export required before full delivery")
    artifact = await export_audience(db, campaign, "full")
    campaign.status = "COMPLETED"
    await emit(db, campaign_id, "campaign.completed", {"artifact_id": artifact.id, "rows": artifact.row_count})
    return artifact


@router.get("/campaigns/{campaign_id}/artifacts", response_model=list[ArtifactView])
async def artifacts(campaign_id: str, db: AsyncSession = Depends(session_dep)):
    await require_campaign(db, campaign_id)
    rows=list((await db.scalars(select(Artifact).where(Artifact.campaign_id == campaign_id))).all())
    for row in rows:
        binding=await db.get(ArtifactBinding,row.id)
        row.binding=binding.data if binding else None
    return rows


@router.get("/artifacts/{artifact_id}/download")
async def download(artifact_id: str, db: AsyncSession = Depends(session_dep)):
    artifact = await db.get(Artifact, artifact_id)
    if not artifact:
        raise HTTPException(404, "Artifact not found")
    body = await asyncio.to_thread(get_verified_blob, artifact)
    return Response(body, media_type="text/csv", headers={
        "Content-Disposition": f'attachment; filename="{artifact.kind}-{artifact.id}.csv"',
        "X-Content-SHA256": artifact.sha256})


@router.post("/support/sessions", status_code=201)
async def new_support_session(member_id: int | None = Query(None,ge=1,le=500), db: AsyncSession = Depends(session_dep)):
    from app.models.entities import RetailSession, RetailProfile
    if member_id is not None and not await db.get(RetailProfile,member_id):
        raise HTTPException(404,"Synthetic member not found")
    session = SupportSession()
    db.add(session)
    await db.flush()
    if member_id is not None:db.add(RetailSession(session_id=session.id,customer_id=member_id))
    await db.commit()
    return {"session_id": session.id, "status": session.status,"member_id":member_id}


@router.post("/support/sessions/{session_id}/messages", response_model=SupportDecision)
async def support_message(session_id: str, data: SupportMessageInput, request: Request,
                          db: AsyncSession = Depends(session_dep)):
    await limit(request, "support", 30)
    return await run_support_turn(db, session_id, data, request)


async def run_support_turn(db, session_id, data, request, on_node=None):
    if not data.use_model or get_settings().ai_provider == "mock":
        return await send_message(db, session_id, data.content, on_node=on_node)
    slots=request.app.state.model_slots
    try:
        await asyncio.wait_for(slots.acquire(), timeout=0.2)
    except TimeoutError:
        raise HTTPException(429, "Model capacity reached")
    try:
        return await asyncio.wait_for(send_message(db, session_id, data.content, True, on_node=on_node), timeout=get_settings().support_timeout_seconds)
    except TimeoutError:
        await db.rollback()
        raise HTTPException(504, "Support model timed out; retry without model")
    finally:
        slots.release()


@router.post("/support/sessions/{session_id}/messages/stream")
async def support_message_stream(session_id: str, data: SupportMessageInput, request: Request):
    await limit(request, "support", 30)
    async with SessionLocal() as db:
        if await db.get(SupportSession, session_id) is None:
            raise HTTPException(404, "Support session not found")

    async def events():
        queue = asyncio.Queue()

        async def run():
            try:
                async with SessionLocal() as db:
                    result = await run_support_turn(
                        db, session_id, data, request,
                        on_node=lambda node: queue.put_nowait(("node", {"node": node})),
                    )
                queue.put_nowait(("complete", result.model_dump(mode="json")))
            except HTTPException as exc:
                queue.put_nowait(("error", {"status": exc.status_code, "detail": exc.detail}))
            except Exception as exc:
                logging.getLogger(__name__).error("Support stream failed: %s", type(exc).__name__)
                queue.put_nowait(("error", {"status": 500, "detail": "Support turn failed"}))

        def frame(event, payload):
            return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"

        yield frame("received", {"session_id": session_id})
        task = asyncio.create_task(run())
        try:
            while True:
                try:
                    event, payload = await asyncio.wait_for(queue.get(), timeout=10)
                except TimeoutError:
                    yield ": keep-alive\n\n"
                    continue
                yield frame(event, payload)
                if event in ("complete", "error"):
                    break
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    return StreamingResponse(events(), media_type="text/event-stream", headers={"Cache-Control": "no-store"})


@router.post("/support/sessions/{session_id}/confirm", response_model=SupportDecision)
async def support_confirm(session_id: str, approved: bool, db: AsyncSession = Depends(session_dep)):
    return await confirm_action(db, session_id, approved)


@router.get("/support/sessions/{session_id}")
async def support_session_status(session_id: str, db: AsyncSession = Depends(session_dep)):
    session = await db.get(SupportSession, session_id)
    if session is None:
        raise HTTPException(404, "Support session not found")
    return {"session_id": session.id, "session_status": session.status, "needs_confirmation": bool(session.pending_action)}


@router.get("/support/sessions/{session_id}/messages")
async def support_history(session_id: str, db: AsyncSession = Depends(session_dep)):
    return await history(db, session_id)


@router.get("/support/sessions/{session_id}/audit")
async def support_audit(session_id: str, db: AsyncSession = Depends(session_dep)):
    return await audit_history(db,session_id)
