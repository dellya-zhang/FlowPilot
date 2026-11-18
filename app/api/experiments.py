"""Local A/B experiment: disjoint cohorts, deduplicated feedback, human promotion."""
import hashlib
from datetime import datetime, timezone
import random
from fastapi import APIRouter, Depends, HTTPException, Body
from pydantic import BaseModel, Field
from sqlalchemy import select, update, func
from app.db.base import session_dep
from app.schemas.contracts import InputModel
from app.models.entities import Campaign, Candidate, Customer, Experiment, Conversion, ExperimentBinding
from app.services.campaigns import require_campaign, emit
from app.services.planning import audience_pool, planning_input, freeze_audience
from app.services.versions import checked_snapshot, comparison_contract, bind_decision

router = APIRouter(prefix="/api/v1/experiments", tags=["experiments"])


class ExperimentInput(BaseModel):
    campaign_id: str
    expected_revisions: dict[str,int] | None = None
    candidate_ids: list[str] = Field(min_length=2, max_length=2)
    per_arm: int | None = Field(default=None, ge=1, le=50)


class PromotionInput(InputModel):
    decision_reason: str | None = Field(default=None,max_length=1000)


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
    snapshots=[await checked_snapshot(db,next(c for c in candidates if c.id==cid),(data.expected_revisions or {}).get(cid)) for cid in data.candidate_ids]
    comparison=comparison_contract(snapshots)
    await freeze_audience(db,campaign)
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
    await db.flush()
    db.add(ExperimentBinding(experiment_id=exp.id,data={"bound_at":datetime.now(timezone.utc).isoformat(),"snapshots":{s["candidate_id"]:s for s in snapshots},"comparison":comparison}))
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
    binding=await db.get(ExperimentBinding,eid)
    return {"binding":binding.data if binding else None,"id": eid, "status": exp.status, "winner": exp.winner, "arms": arms,
            "note": "描述性转化率，不代表统计显著性；使用模拟回流，必须人工选择胜出方案。"}


class FinalizeInput(InputModel):
    simulation_reason: str | None = Field(default=None, min_length=3, max_length=1000)


@router.post("/{eid}/finalize")
async def finalize_experiment(eid: str, data: FinalizeInput, db=Depends(session_dep)):
    exp = await locked(db, eid)
    binding = await db.get(ExperimentBinding, eid)
    if not binding:
        raise HTTPException(409, "Experiment has no frozen version binding")
    if exp.status in ("FINALIZED", "PROMOTED") and binding.data.get("finalize_result"):
        return binding.data["finalize_result"]
    if exp.status != "RUNNING":
        raise HTTPException(409, "Experiment is not running")
    from datetime import timedelta
    bound_at = datetime.fromisoformat(binding.data["bound_at"])
    snapshots = binding.data["snapshots"]
    days = max(((s.get("plan_data") or {}).get("strategy") or {}).get("ab_experiment", {}).get("window_days", 7) for s in snapshots.values())
    now = datetime.now(timezone.utc)
    early = now < bound_at + timedelta(days=days)
    if early and not data.simulation_reason:
        raise HTTPException(409, "Observation window is not over; explicit simulation reason required")
    counts = dict((await db.execute(select(Conversion.arm, func.count()).where(
        Conversion.experiment_id == eid).group_by(Conversion.arm))).all())
    arms = [{"candidate_id": cid, "assigned": len(users), "converted": counts.get(cid, 0),
             "rate": counts.get(cid, 0) / len(users)} for cid, users in exp.arms.items()]
    ranked = sorted(arms, key=lambda a: a["rate"], reverse=True)
    recommended = ranked[0]["candidate_id"] if ranked[0]["rate"] > ranked[1]["rate"] else None
    result = {"finalized_at": now.isoformat(), "window_days": days, "early_simulation": early,
              "simulation_reason": data.simulation_reason, "arms": arms, "recommended_candidate_id": recommended,
              "note": "Descriptive conversion per assigned synthetic user; no significance or causal claim."}
    binding.data = {**binding.data, "finalize_result": result}
    exp.status = "FINALIZED"
    await emit(db, exp.campaign_id, "experiment.finalized", {"experiment_id": eid, "early_simulation": early})
    return result


@router.post("/{eid}/promote")
async def promote(eid: str, candidate_id: str, data: PromotionInput | None = Body(default=None), require_finalized: bool = False, db=Depends(session_dep)):
    exp = await locked(db, eid)
    if require_finalized and exp.status == "RUNNING":
        raise HTTPException(409, "Finalize the experiment before promotion")
    if exp.status not in ("RUNNING", "FINALIZED"):
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
    binding=await db.get(ExperimentBinding,eid)
    candidate=await db.get(Candidate,candidate_id)
    snapshot=binding.data['snapshots'][candidate_id] if binding else await checked_snapshot(db,candidate)
    await bind_decision(db,campaign,snapshot,'experiment:'+eid,data.decision_reason if data else None)
    campaign.selected_candidate_id = candidate_id
    campaign.status = "PILOT_EXPORTED"
    exp.winner, exp.status = candidate_id, "PROMOTED"
    await emit(db, campaign.id, "experiment.promoted", {"experiment_id": eid, "winner": candidate_id})
    return {"winner": candidate_id, "status": exp.status}
