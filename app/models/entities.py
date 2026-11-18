import datetime as dt
import uuid
from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def uid():
    return uuid.uuid4().hex


def utcnow():
    return dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)


class Base(DeclarativeBase):
    pass


class Campaign(Base):
    __tablename__ = "campaigns"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    brief: Mapped[str] = mapped_column(Text)
    audience: Mapped[str] = mapped_column(String(48))
    budget: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(32), default="CREATED", index=True)
    selected_candidate_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class Candidate(Base):
    __tablename__ = "candidates"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    campaign_id: Mapped[str] = mapped_column(ForeignKey("campaigns.id"), index=True)
    variant: Mapped[int] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(String(150))
    message: Mapped[str] = mapped_column(Text)
    segment: Mapped[str] = mapped_column(String(48))
    reasoning: Mapped[str] = mapped_column(Text)
    score: Mapped[int] = mapped_column(Integer)
    __table_args__ = (UniqueConstraint("campaign_id", "variant", name="uq_candidate_variant"),)


class Event(Base):
    __tablename__ = "events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    campaign_id: Mapped[str] = mapped_column(ForeignKey("campaigns.id"), index=True)
    name: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)


class Customer(Base):
    __tablename__ = "customers"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    segment: Mapped[str] = mapped_column(String(48), index=True)
    region: Mapped[str] = mapped_column(String(48))
    consent: Mapped[bool] = mapped_column(Boolean, default=True)


class Artifact(Base):
    __tablename__ = "artifacts"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    campaign_id: Mapped[str] = mapped_column(ForeignKey("campaigns.id"), index=True)
    kind: Mapped[str] = mapped_column(String(16))
    key: Mapped[str] = mapped_column(String(256))
    sha256: Mapped[str] = mapped_column(String(64))
    row_count: Mapped[int] = mapped_column(Integer)
    __table_args__ = (UniqueConstraint("campaign_id", "kind", name="uq_artifact_kind"),)


class SupportSession(Base):
    __tablename__ = "support_sessions"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    status: Mapped[str] = mapped_column(String(32), default="OPEN")
    pending_action: Mapped[dict | None] = mapped_column(JSON, nullable=True)


class SupportMessage(Base):
    __tablename__ = "support_messages"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(ForeignKey("support_sessions.id"), index=True)
    role: Mapped[str] = mapped_column(String(20))
    content: Mapped[str] = mapped_column(Text)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)


class Experiment(Base):
    __tablename__ = "experiments"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    campaign_id: Mapped[str] = mapped_column(ForeignKey("campaigns.id"), unique=True)
    arms: Mapped[dict] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(32), default="RUNNING")
    winner: Mapped[str | None] = mapped_column(String(32), nullable=True)


class Conversion(Base):
    __tablename__ = "conversions"
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    experiment_id: Mapped[str] = mapped_column(ForeignKey("experiments.id"), index=True)
    customer_id: Mapped[int] = mapped_column(Integer)
    arm: Mapped[str] = mapped_column(String(32))
    __table_args__ = (UniqueConstraint("experiment_id", "customer_id"),)


class ExecutionLease(Base):
    __tablename__ = "execution_leases"
    campaign_id: Mapped[str] = mapped_column(ForeignKey("campaigns.id"), primary_key=True)
    owner: Mapped[str] = mapped_column(String(32))
    expires_at: Mapped[float] = mapped_column(Float, index=True)


class PlanningContext(Base):
    """Separate table keeps existing campaign databases compatible."""
    __tablename__ = "planning_contexts"
    campaign_id: Mapped[str] = mapped_column(ForeignKey("campaigns.id"), primary_key=True)
    data: Mapped[dict] = mapped_column(JSON)


class CandidatePlan(Base):
    __tablename__ = "candidate_plans"
    candidate_id: Mapped[str] = mapped_column(ForeignKey("candidates.id"), primary_key=True)
    data: Mapped[dict] = mapped_column(JSON)


class PlanRevision(Base):
    __tablename__ = "plan_revisions"
    candidate_id: Mapped[str] = mapped_column(ForeignKey("candidates.id"), primary_key=True)
    revision: Mapped[int] = mapped_column(Integer, primary_key=True)
    snapshot: Mapped[dict] = mapped_column(JSON)
    reason: Mapped[str] = mapped_column(Text)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)


class DecisionBinding(Base):
    __tablename__ = "decision_bindings"
    campaign_id: Mapped[str] = mapped_column(ForeignKey("campaigns.id"), primary_key=True)
    data: Mapped[dict] = mapped_column(JSON)


class ExperimentBinding(Base):
    __tablename__ = "experiment_bindings"
    experiment_id: Mapped[str] = mapped_column(ForeignKey("experiments.id"), primary_key=True)
    data: Mapped[dict] = mapped_column(JSON)


class ArtifactBinding(Base):
    __tablename__ = "artifact_bindings"
    artifact_id: Mapped[str] = mapped_column(ForeignKey("artifacts.id"), primary_key=True)
    data: Mapped[dict] = mapped_column(JSON)


class AudienceSnapshot(Base):
    __tablename__ = "audience_snapshots"
    campaign_id: Mapped[str] = mapped_column(ForeignKey("campaigns.id"), primary_key=True)
    data: Mapped[dict] = mapped_column(JSON)


class OperatorReview(Base):
    __tablename__ = "operator_reviews"
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    campaign_id: Mapped[str] = mapped_column(ForeignKey("campaigns.id"), index=True)
    data: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)


class ExecutionObservation(Base):
    __tablename__ = "execution_observations"
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    campaign_id: Mapped[str] = mapped_column(ForeignKey("campaigns.id"), index=True)
    candidate_id: Mapped[str] = mapped_column(ForeignKey("candidates.id"))
    revision: Mapped[int] = mapped_column(Integer)
    customer_id: Mapped[int] = mapped_column(Integer)
    outcome: Mapped[str] = mapped_column(String(16))
    contact_number: Mapped[int] = mapped_column(Integer,default=0)
    data: Mapped[dict] = mapped_column(JSON)
    __table_args__ = (UniqueConstraint("campaign_id", "candidate_id", "revision", "customer_id", "outcome", "contact_number"),)


class SupportAudit(Base):
    __tablename__ = "support_audits"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(ForeignKey("support_sessions.id"), index=True)
    data: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)
