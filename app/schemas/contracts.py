from typing import Literal
from pydantic import BaseModel, Field, ConfigDict, field_validator, model_validator
import unicodedata


class InputModel(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    @field_validator("*", mode="before")
    @classmethod
    def reject_controls(cls, value):
        if isinstance(value, str) and any(unicodedata.category(c) == "Cc" and c not in "\n\r\t" for c in value):
            raise ValueError("Control characters are not allowed")
        return value


class PlanningInput(InputModel):
    scenario: Literal["membership_benefits"] = "membership_benefits"
    comparison_axis: Literal["message", "frequency", "channel"] = "message"
    reference_message: str = Field(default="", max_length=400)
    success_definition: str = Field(default="用户主动查询权益或联系专属客服", min_length=5, max_length=300)
    contacts_per_user: int = Field(default=1, ge=1, le=3)
    available_channels: list[Literal["manual", "sms", "in_app"]] = Field(default_factory=list, max_length=3)
    channel_costs: dict[Literal["manual", "sms", "in_app"], float] = Field(default_factory=dict)

    @field_validator("channel_costs")
    @classmethod
    def valid_costs(cls, value):
        import math
        if any(not math.isfinite(v) or not 0 <= v <= 1000 for v in value.values()):
            raise ValueError("Channel costs must be finite and between 0 and 1000")
        return value

    @model_validator(mode="after")
    def primary_channel_available(self):
        if self.comparison_axis != "channel" and self.available_channels and self.channel not in self.available_channels:
            raise ValueError("所选主渠道必须包含在已确认可用的渠道中")
        return self

    intent: Literal["acquisition", "retention", "reactivation", "education"] = "education"
    known_benefits: str = Field(default="", max_length=1000)
    channel: Literal["manual", "sms", "in_app"] = "manual"
    region: Literal["all", "north", "south", "east", "west"] = "all"
    unit_cost_cny: float = Field(default=0, ge=0, le=1000, allow_inf_nan=False)
    pilot_ratio: float = Field(default=0.1, ge=0.05, le=0.3, allow_inf_nan=False)
    window_days: int = Field(default=7, ge=3, le=14)
    enable_holdout: bool = False
    holdout_ratio: float = Field(default=0.1, ge=0.05, le=0.5, allow_inf_nan=False)


class CampaignCreate(InputModel):
    planning: PlanningInput | None = None
    brief: str = Field(min_length=5, max_length=2000)
    audience: Literal["new", "active", "dormant", "all"] = "all"
    budget: int = Field(ge=100, le=1_000_000)


class CampaignView(BaseModel):
    id: str
    brief: str
    audience: str
    budget: int
    status: str
    selected_candidate_id: str | None
    model_config = {"from_attributes": True}


class CandidateView(BaseModel):
    plan_data: dict | None = None
    id: str
    variant: int
    title: str
    message: str
    segment: str
    reasoning: str
    score: int
    model_config = {"from_attributes": True}


class Approval(BaseModel):
    decision_reason: str | None = Field(default=None,max_length=1000)
    candidate_id: str
    expected_revision: int | None = Field(default=None, ge=1)


class ArtifactView(BaseModel):
    binding: dict | None = None
    id: str
    kind: str
    sha256: str
    row_count: int
    model_config = {"from_attributes": True}


class SupportMessageInput(InputModel):
    content: str = Field(min_length=1, max_length=2000)
    use_model: bool = False


class SupportDecision(BaseModel):
    reply: str
    intent: str
    needs_confirmation: bool = False
    session_status: str

    facts: list[dict[str, str]] = Field(default_factory=list)
    decision: dict | None = None
    citations: list[dict] = Field(default_factory=list)
