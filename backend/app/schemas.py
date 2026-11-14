"""Request and response schemas. All input is validated here before it reaches a service."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from .models import AppStatus, DriveStatus, OfferStatus, Role, RoundKind, SlotStatus
from .services.policy import PolicyConfig


class ORM(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# ---------------------------------------------------------------- auth
class OtpRequest(BaseModel):
    email: EmailStr


class OtpRequested(BaseModel):
    sent: bool
    demo_code: str | None = None   # only in DEMO_MODE
    message: str


class OtpVerify(BaseModel):
    email: EmailStr
    code: str = Field(pattern=r"^\d{6}$")


class Me(ORM):
    id: int
    email: str
    name: str
    role: Role
    student_id: int | None


class DemoAccount(BaseModel):
    email: str
    name: str
    role: Role
    note: str


# ---------------------------------------------------------------- students
class StudentOut(ORM):
    id: int
    roll_no: str
    name: str
    email: str
    phone: str | None
    branch: str
    programme: str
    cgpa: float
    backlogs: int
    grad_year: int
    skills: list[str]
    verified: bool
    archived: bool


class StudentCreate(BaseModel):
    roll_no: str = Field(min_length=3, max_length=32, pattern=r"^[A-Za-z0-9/_-]+$")
    name: str = Field(min_length=2, max_length=120)
    email: EmailStr
    phone: str | None = Field(default=None, max_length=20)
    branch: Literal["CSE", "ECE", "EEE", "ME", "CE", "CHE", "PE"]
    programme: Literal["BTECH", "MTECH", "MCA", "MSC"]
    cgpa: float = Field(ge=0, le=10)
    backlogs: int = Field(default=0, ge=0, le=30)
    grad_year: int = Field(ge=2000, le=2100)
    skills: list[str] = Field(default_factory=list, max_length=40)


class StudentUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=120)
    phone: str | None = Field(default=None, max_length=20)
    branch: Literal["CSE", "ECE", "EEE", "ME", "CE", "CHE", "PE"] | None = None
    programme: Literal["BTECH", "MTECH", "MCA", "MSC"] | None = None
    cgpa: float | None = Field(default=None, ge=0, le=10)
    backlogs: int | None = Field(default=None, ge=0, le=30)
    grad_year: int | None = Field(default=None, ge=2000, le=2100)
    skills: list[str] | None = Field(default=None, max_length=40)
    verified: bool | None = None
    archived: bool | None = None


class StudentSelfUpdate(BaseModel):
    phone: str | None = Field(default=None, max_length=20, pattern=r"^[0-9+ -]*$")
    skills: list[str] | None = Field(default=None, max_length=40)

    @field_validator("skills")
    @classmethod
    def _clean(cls, v: list[str] | None) -> list[str] | None:
        if v is None:
            return v
        return sorted({s.strip().lower()[:40] for s in v if s.strip()})


class ResumeOut(ORM):
    id: int
    track: str
    filename: str
    created_at: datetime


# ---------------------------------------------------------------- companies & policy
class CompanyIn(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    sector: str | None = Field(default=None, max_length=80)
    contact_name: str | None = Field(default=None, max_length=120)
    contact_email: EmailStr | None = None
    notes: str | None = Field(default=None, max_length=2000)


class CompanyUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=120)
    sector: str | None = Field(default=None, max_length=80)
    contact_name: str | None = Field(default=None, max_length=120)
    contact_email: EmailStr | None = None
    notes: str | None = Field(default=None, max_length=2000)
    archived: bool | None = None


class CompanyOut(ORM):
    id: int
    name: str
    sector: str | None
    contact_name: str | None
    contact_email: str | None
    notes: str | None
    archived: bool
    active_drives: int = 0      # drives not yet completed or cancelled
    live_drives: int = 0


class PolicyIn(BaseModel):
    config: PolicyConfig
    note: str | None = Field(default=None, max_length=300)


class PolicyOut(ORM):
    id: int
    version: int
    config: dict[str, Any]
    note: str | None
    active: bool
    created_by: str | None
    created_at: datetime


# ---------------------------------------------------------------- drives
class DriveIn(BaseModel):
    company_id: int
    role: str = Field(min_length=2, max_length=160)
    jd_text: str = Field(default="", max_length=20000)
    ctc_lpa: float = Field(gt=0, le=500)
    location: str | None = Field(default=None, max_length=120)
    apply_deadline: datetime | None = None
    target_hires: int = Field(default=1, ge=1, le=500)


class DriveUpdate(BaseModel):
    role: str | None = Field(default=None, min_length=2, max_length=160)
    jd_text: str | None = Field(default=None, max_length=20000)
    ctc_lpa: float | None = Field(default=None, gt=0, le=500)
    location: str | None = Field(default=None, max_length=120)
    apply_deadline: datetime | None = None
    target_hires: int | None = Field(default=None, ge=1, le=500)
    status: DriveStatus | None = None
    skills: list[str] | None = None


class RuleIn(BaseModel):
    field: Literal["cgpa", "backlogs", "branch", "programme", "grad_year"]
    op: Literal[">=", "<=", "in"]
    value: float | int | list[str] | list[int]
    source_text: str | None = Field(default=None, max_length=600)
    approved: bool = True

    @field_validator("value")
    @classmethod
    def _shape(cls, v: Any, info: Any) -> Any:
        op = info.data.get("op")
        if op == "in" and not isinstance(v, list):
            raise ValueError("'in' rules need a list of values")
        if op in (">=", "<=") and isinstance(v, list):
            raise ValueError("comparison rules need a single number")
        return v


class RuleOut(ORM):
    id: int
    field: str
    op: str
    value: Any
    source_text: str | None
    confidence: float
    approved: bool
    approved_by: str | None


class RoundIn(BaseModel):
    name: str = Field(min_length=2, max_length=80)
    kind: RoundKind
    seq: int = Field(ge=1, le=20)
    duration_min: int = Field(default=30, ge=5, le=240)


class PanelIn(BaseModel):
    round_id: int
    name: str = Field(min_length=1, max_length=60)
    room: str = Field(min_length=1, max_length=60)
    interviewer: str | None = Field(default=None, max_length=120)


class PanelOut(ORM):
    id: int
    round_id: int
    name: str
    room: str
    interviewer: str | None


class RoundOut(ORM):
    id: int
    name: str
    kind: RoundKind
    seq: int
    duration_min: int
    panels: list[PanelOut]


class DriveOut(BaseModel):
    id: int
    company_id: int
    company: str
    role: str
    jd_text: str
    ctc_lpa: float
    tier: str
    sector: str | None = None
    location: str | None
    status: DriveStatus
    apply_deadline: datetime | None
    drive_start: datetime | None
    target_hires: int
    skills: list[str]
    applicants: int
    eligible: int | None = None
    rules_pending: int = 0


class ScheduleRequest(BaseModel):
    round_id: int
    start_at: datetime
    # Working hours (local time). Leave out to reuse this drive's last hours, or the defaults from settings.
    day_start: str | None = Field(default=None, pattern=r"^\d{1,2}:\d{2}$")
    day_end: str | None = Field(default=None, pattern=r"^\d{1,2}:\d{2}$")
    breaks: list[str] | None = Field(default=None, max_length=6)
    max_days: int | None = Field(default=None, ge=1, le=14)
    skip_sundays: bool | None = None


class ExtractRequest(BaseModel):
    jd_text: str = Field(min_length=20, max_length=20000)
    replace_existing: bool = True


# ---------------------------------------------------------------- applications & live
class ApplyIn(BaseModel):
    resume_id: int | None = None


class ApplicationOut(BaseModel):
    id: int
    drive_id: int
    student_id: int
    student: str
    roll_no: str
    branch: str
    cgpa: float
    status: AppStatus
    stage: str
    rounds_cleared: int
    skill_match: float | None = None
    offer_status: OfferStatus | None = None


class StageUpdate(BaseModel):
    application_ids: list[int] = Field(min_length=1, max_length=2000)
    action: Literal["shortlist", "reject", "pass_round", "fail_round"]
    round_id: int | None = None


class ResultsCsv(BaseModel):
    round_id: int
    csv_text: str = Field(min_length=1, max_length=2_000_000)


class SlotOut(BaseModel):
    id: int
    round: str
    round_id: int
    panel: str
    panel_id: int
    room: str
    student: str
    student_id: int
    roll_no: str
    status: SlotStatus
    start_at: datetime
    end_at: datetime
    projected_start: datetime | None = None
    lateness_min: int = 0
    checked_in: bool
    result: str | None


class SlotResult(BaseModel):
    result: Literal["pass", "fail"]


class CheckinIn(BaseModel):
    token: str = Field(min_length=10, max_length=2000)


class OfferIn(BaseModel):
    application_id: int
    ctc_lpa: float = Field(gt=0, le=500)
    deadline: datetime | None = None


class OfferOut(BaseModel):
    id: int
    application_id: int
    company: str
    role: str
    ctc_lpa: float
    tier: str
    deadline: datetime
    status: OfferStatus


class DisputeIn(BaseModel):
    drive_id: int
    message: str = Field(min_length=10, max_length=2000)


class DisputeResolve(BaseModel):
    status: Literal["upheld", "rejected"]
    resolution: str = Field(min_length=3, max_length=2000)


class ChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=4000)


class AssistantIn(BaseModel):
    messages: list[ChatMessage] = Field(min_length=1, max_length=40)


class QuestionIn(BaseModel):
    question: str = Field(min_length=3, max_length=500)


class ExperienceIn(BaseModel):
    drive_id: int
    difficulty: int = Field(ge=1, le=5)
    topics: list[str] = Field(min_length=1, max_length=20)
    tips: str | None = Field(default=None, max_length=2000)


class PoolPreviewIn(BaseModel):
    cgpa_min: float | None = Field(default=None, ge=0, le=10)
    max_backlogs: int | None = Field(default=None, ge=0, le=30)
    branches: list[str] | None = None
    programmes: list[str] | None = None


class CriteriaChangeIn(PoolPreviewIn):
    note: str | None = Field(default=None, max_length=500)


class SimulationIn(BaseModel):
    candidates: int = Field(default=24, ge=2, le=80)
    panels: int = Field(default=3, ge=1, le=10)
    duration_min: int = Field(default=30, ge=10, le=90)
    overrun_mean: float = Field(default=1.15, ge=0.7, le=2.0)
    overrun_sd: float = Field(default=0.3, ge=0, le=1.0)
    no_show_rate: float = Field(default=0.08, ge=0, le=0.5)
    trials: int = Field(default=15, ge=1, le=60)
    seed: int = Field(default=7, ge=0, le=1_000_000)


class UserIn(BaseModel):
    email: EmailStr
    name: str = Field(min_length=2, max_length=120)
    role: Literal["coordinator", "admin"]


class UserUpdate(BaseModel):
    role: Literal["coordinator", "admin"] | None = None
    active: bool | None = None


class UserOut(ORM):
    id: int
    email: str
    name: str
    role: Role
    active: bool
