from typing import Literal
from pydantic import BaseModel, Field, ConfigDict, field_validator
import unicodedata


class InputModel(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    @field_validator("*", mode="before")
    @classmethod
    def reject_controls(cls, value):
        if isinstance(value, str) and any(unicodedata.category(c) == "Cc" and c not in "\n\r\t" for c in value):
            raise ValueError("Control characters are not allowed")
        return value


class CampaignCreate(InputModel):
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
    id: str
    variant: int
    title: str
    message: str
    segment: str
    reasoning: str
    score: int
    model_config = {"from_attributes": True}


class Approval(BaseModel):
    candidate_id: str


class ArtifactView(BaseModel):
    id: str
    kind: str
    sha256: str
    row_count: int
    model_config = {"from_attributes": True}


class SupportMessageInput(InputModel):
    content: str = Field(min_length=1, max_length=2000)


class SupportDecision(BaseModel):
    reply: str
    intent: str
    needs_confirmation: bool = False
    session_status: str

    facts: list[dict[str, str]] = Field(default_factory=list)
