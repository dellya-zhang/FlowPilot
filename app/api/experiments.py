"""Local A/B experiment: disjoint cohorts, deduplicated feedback, human promotion."""
import hashlib
import random
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select, update, func
from app.db.base import session_dep
from app.models.entities import Campaign, Candidate, Customer, Experiment, Conversion
from app.services.campaigns import require_campaign, emit
from app.services.planning import audience_pool, planning_input

router = APIRouter(prefix="/api/v1/experiments", tags=["experiments"])


class ExperimentInput(BaseModel):
    campaign_id: str
    candidate_ids: list[str] = Field(min_length=2, max_length=2)
    per_arm: int | None = Field(default=None, ge=1, le=50)


class Feedback(BaseModel):
    event_id: str = Field(min_length=1, max_length=80)
    customer_id: int
    candidate_id: str


async def locked(db, eid):
    await db.execute(update(Experiment).where(Experiment.id == eid).values(status=Experiment.status))
    experiment = await db.get(Experiment, eid)
    if not experiment:
        raise HTTPException(404, "Experiment not found")
    return experiment


@router.post("", status_code=201)
async def create(data: ExperimentInput, db=Depends(session_dep)):
    await db.execute(update(Campaign).where(Campaign.id == data.campaign_id).values(updated_at=Campaign.updated_at))
    campaign = await require_campaign(db, data.campaign_id)
    if campaign.status != "WAITING_APPROVAL":
        raise HTTPException(409, "Experiment requires unapproved generated campaign")
    if await db.scalar(select(Experiment).where(Experiment.campaign_id == campaign.id)):
        raise HTTPException(409, "Experiment already exists")
    ids = set(data.candidate_ids)
    candidates = list((await db.scalars(select(Candidate).where(
        Candidate.campaign_id == campaign.id, Candidate.id.in_(ids)))).all())
    if len(ids) != 2 or len(candidates) != 2:
        raise HTTPException(422, "Choose two distinct candidates from this campaign")
    rows, _ = await audience_pool(db, campaign)
    customers = [c.id for c in rows]
    planning = await planning_input(db, campaign.id)
    per_arm = data.per_arm or (max(1, __import__('math').ceil(len(customers)*planning['pilot_ratio']/2)) if planning else 10)
    if len(customers) < 2 * per_arm:
        raise HTTPException(422, "Insufficient consenting audience")
    random.Random(int(hashlib.sha256(campaign.id.encode()).hexdigest(), 16)).shuffle(customers)
    arms = {cid: customers[i * per_arm:(i + 1) * per_arm] for i, cid in enumerate(data.candidate_ids)}
    exp = Experiment(campaign_id=campaign.id, arms=arms)
    db.add(exp)
    await db.commit()
    return {"id": exp.id, "arms": arms, "status": exp.status}


@router.post("/{eid}/feedback")
async def feedback(eid: str, data: Feedback, db=Depends(session_dep)):
    exp = await locked(db, eid)
    if exp.status != "RUNNING":
        raise HTTPException(409, "Experiment closed")
    if data.customer_id not in exp.arms.get(data.candidate_id, []):
        raise HTTPException(422, "Customer is not assigned to this arm")
    old = await db.get(Conversion, data.event_id)
    if old:
        if (old.experiment_id, old.customer_id, old.arm) != (eid, data.customer_id, data.candidate_id):
            raise HTTPException(409, "Event ID already used with different payload")
        return {"accepted": True, "duplicate": True}
    converted = await db.scalar(select(Conversion).where(Conversion.experiment_id == eid,
                                                        Conversion.customer_id == data.customer_id))
    if converted:
        return {"accepted": True, "duplicate": True}
    db.add(Conversion(id=data.event_id, experiment_id=eid, customer_id=data.customer_id, arm=data.candidate_id))
    await db.commit()
    return {"accepted": True, "duplicate": False}


@router.get("/{eid}")
async def report(eid: str, db=Depends(session_dep)):
    exp = await db.get(Experiment, eid)
    if not exp:
        raise HTTPException(404, "Experiment not found")
    counts = dict((await db.execute(select(Conversion.arm, func.count()).where(
        Conversion.experiment_id == eid).group_by(Conversion.arm))).all())
    arms = [{"candidate_id": cid, "users": users, "conversions": counts.get(cid, 0),
             "rate": counts.get(cid, 0) / len(users)} for cid, users in exp.arms.items()]
    return {"id": eid, "status": exp.status, "winner": exp.winner, "arms": arms,
            "note": "描述性转化率，不代表统计显著性；使用模拟回流，必须人工选择胜出方案。"}


@router.post("/{eid}/promote")
async def promote(eid: str, candidate_id: str, db=Depends(session_dep)):
    exp = await locked(db, eid)
    if exp.status != "RUNNING":
        if exp.winner == candidate_id:
            return {"winner": exp.winner, "status": exp.status}
        raise HTTPException(409, "Experiment already promoted")
    if candidate_id not in exp.arms:
        raise HTTPException(422, "Candidate not in experiment")
    # Lock campaign and verify the operator has not independently approved another plan.
    await db.execute(update(Campaign).where(Campaign.id == exp.campaign_id).values(updated_at=Campaign.updated_at))
    campaign = await require_campaign(db, exp.campaign_id)
    if campaign.status != "WAITING_APPROVAL":
        raise HTTPException(409, "Campaign already advanced")
    campaign.selected_candidate_id = candidate_id
    campaign.status = "PILOT_EXPORTED"
    exp.winner, exp.status = candidate_id, "PROMOTED"
    await emit(db, campaign.id, "experiment.promoted", {"experiment_id": eid, "winner": candidate_id})
    return {"winner": candidate_id, "status": exp.status}
