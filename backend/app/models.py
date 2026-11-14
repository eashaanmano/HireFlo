"""Database models. Twelve core entities plus small supporting tables (see docs/architecture.md)."""
from __future__ import annotations

import enum
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    TypeDecorator,
    UniqueConstraint,
)
from sqlalchemy.engine import Dialect
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .core.database import Base
from .core.timeutil import utcnow


class UTCDateTime(TypeDecorator[datetime]):
    """Stores aware datetimes as UTC and always returns aware UTC values (SQLite drops tzinfo)."""

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return value.astimezone(UTC)

    def process_result_value(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


# ----------------------------------------------------------------------------- enums
class Role(str, enum.Enum):
    student = "student"
    coordinator = "coordinator"
    admin = "admin"


class DriveStatus(str, enum.Enum):
    draft = "draft"
    open = "open"
    closed = "closed"
    live = "live"
    completed = "completed"
    cancelled = "cancelled"


class RoundKind(str, enum.Enum):
    test = "test"
    interview = "interview"


class AppStatus(str, enum.Enum):
    applied = "applied"
    in_process = "in_process"
    rejected = "rejected"
    selected = "selected"
    withdrawn = "withdrawn"


class SlotStatus(str, enum.Enum):
    scheduled = "scheduled"
    in_progress = "in_progress"
    done = "done"
    no_show = "no_show"
    cancelled = "cancelled"


class OfferStatus(str, enum.Enum):
    pending = "pending"
    accepted = "accepted"
    declined = "declined"
    revoked = "revoked"


class DisputeStatus(str, enum.Enum):
    open = "open"
    upheld = "upheld"
    rejected = "rejected"


class ProposalStatus(str, enum.Enum):
    pending = "pending"
    approved = "approved"
    dismissed = "dismissed"
    superseded = "superseded"


def _enum(e: type[enum.Enum]) -> Enum:
    return Enum(e, native_enum=False, validate_strings=True, length=20)


class Timestamped:
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)


# ----------------------------------------------------------------------------- people
class User(Base, Timestamped):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(120))
    role: Mapped[Role] = mapped_column(_enum(Role))
    student_id: Mapped[int | None] = mapped_column(ForeignKey("students.id", ondelete="SET NULL"))
    active: Mapped[bool] = mapped_column(Boolean, default=True)

    student: Mapped[Student | None] = relationship(back_populates="user")


class OtpCode(Base):
    __tablename__ = "otp_codes"
    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(255), index=True)
    code_hash: Mapped[str] = mapped_column(String(128))
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    used: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class Student(Base, Timestamped):
    __tablename__ = "students"
    id: Mapped[int] = mapped_column(primary_key=True)
    roll_no: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(120))
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    phone: Mapped[str | None] = mapped_column(String(20))
    branch: Mapped[str] = mapped_column(String(16), index=True)
    programme: Mapped[str] = mapped_column(String(16))
    cgpa: Mapped[float] = mapped_column(Float)
    backlogs: Mapped[int] = mapped_column(Integer, default=0)
    grad_year: Mapped[int] = mapped_column(Integer)
    skills: Mapped[list[str]] = mapped_column(JSON, default=list)
    verified: Mapped[bool] = mapped_column(Boolean, default=False)
    archived: Mapped[bool] = mapped_column(Boolean, default=False)

    user: Mapped[User | None] = relationship(back_populates="student")
    resumes: Mapped[list[Resume]] = relationship(back_populates="student", cascade="all, delete-orphan")
    applications: Mapped[list[Application]] = relationship(back_populates="student")


class Resume(Base):
    __tablename__ = "resumes"
    id: Mapped[int] = mapped_column(primary_key=True)
    student_id: Mapped[int] = mapped_column(ForeignKey("students.id", ondelete="CASCADE"), index=True)
    track: Mapped[str] = mapped_column(String(40))
    filename: Mapped[str] = mapped_column(String(200))
    path: Mapped[str] = mapped_column(String(400))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)

    student: Mapped[Student] = relationship(back_populates="resumes")


# ----------------------------------------------------------------------------- companies & policy
class Company(Base, Timestamped):
    __tablename__ = "companies"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True)
    sector: Mapped[str | None] = mapped_column(String(80))
    contact_name: Mapped[str | None] = mapped_column(String(120))
    contact_email: Mapped[str | None] = mapped_column(String(255))
    notes: Mapped[str | None] = mapped_column(Text)
    archived: Mapped[bool] = mapped_column(Boolean, default=False)

    drives: Mapped[list[Drive]] = relationship(back_populates="company")


class Policy(Base):
    """Institute placement policy. Edits create a new version; exactly one version is active."""

    __tablename__ = "policies"
    id: Mapped[int] = mapped_column(primary_key=True)
    version: Mapped[int] = mapped_column(Integer)
    config: Mapped[dict[str, Any]] = mapped_column(JSON)
    note: Mapped[str | None] = mapped_column(String(300))
    active: Mapped[bool] = mapped_column(Boolean, default=False)
    created_by: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


# ----------------------------------------------------------------------------- drives
class Drive(Base, Timestamped):
    __tablename__ = "drives"
    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"), index=True)
    role: Mapped[str] = mapped_column(String(160))
    jd_text: Mapped[str] = mapped_column(Text, default="")
    ctc_lpa: Mapped[float] = mapped_column(Float)
    location: Mapped[str | None] = mapped_column(String(120))
    status: Mapped[DriveStatus] = mapped_column(_enum(DriveStatus), default=DriveStatus.draft)
    apply_deadline: Mapped[datetime | None] = mapped_column(UTCDateTime)
    drive_start: Mapped[datetime | None] = mapped_column(UTCDateTime)
    target_hires: Mapped[int] = mapped_column(Integer, default=1)
    skills: Mapped[list[str]] = mapped_column(JSON, default=list)
    link_version: Mapped[int] = mapped_column(Integer, default=1)

    company: Mapped[Company] = relationship(back_populates="drives")
    rules: Mapped[list[EligibilityRule]] = relationship(back_populates="drive", cascade="all, delete-orphan")
    rounds: Mapped[list[Round]] = relationship(
        back_populates="drive", cascade="all, delete-orphan", order_by="Round.seq"
    )
    applications: Mapped[list[Application]] = relationship(back_populates="drive", cascade="all, delete-orphan")


class EligibilityRule(Base):
    __tablename__ = "eligibility_rules"
    id: Mapped[int] = mapped_column(primary_key=True)
    drive_id: Mapped[int] = mapped_column(ForeignKey("drives.id", ondelete="CASCADE"), index=True)
    field: Mapped[str] = mapped_column(String(20))        # cgpa | backlogs | branch | programme | grad_year
    op: Mapped[str] = mapped_column(String(4))            # >= | <= | in
    value: Mapped[Any] = mapped_column(JSON)
    source_text: Mapped[str | None] = mapped_column(Text)
    confidence: Mapped[float] = mapped_column(Float, default=1.0)
    approved: Mapped[bool] = mapped_column(Boolean, default=False)
    approved_by: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)

    drive: Mapped[Drive] = relationship(back_populates="rules")


class EligibilityDecision(Base):
    """Stored so every outcome is explainable, auditable and disputable."""

    __tablename__ = "eligibility_decisions"
    drive_id: Mapped[int] = mapped_column(ForeignKey("drives.id", ondelete="CASCADE"), primary_key=True)
    student_id: Mapped[int] = mapped_column(ForeignKey("students.id", ondelete="CASCADE"), primary_key=True)
    eligible: Mapped[bool] = mapped_column(Boolean)
    reasons: Mapped[list[dict[str, Any]]] = mapped_column(JSON)
    computed_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class Round(Base):
    __tablename__ = "rounds"
    id: Mapped[int] = mapped_column(primary_key=True)
    drive_id: Mapped[int] = mapped_column(ForeignKey("drives.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(80))
    kind: Mapped[RoundKind] = mapped_column(_enum(RoundKind))
    seq: Mapped[int] = mapped_column(Integer)
    duration_min: Mapped[int] = mapped_column(Integer, default=30)

    drive: Mapped[Drive] = relationship(back_populates="rounds")
    panels: Mapped[list[Panel]] = relationship(back_populates="round", cascade="all, delete-orphan")


class Panel(Base):
    __tablename__ = "panels"
    id: Mapped[int] = mapped_column(primary_key=True)
    drive_id: Mapped[int] = mapped_column(ForeignKey("drives.id", ondelete="CASCADE"), index=True)
    round_id: Mapped[int] = mapped_column(ForeignKey("rounds.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(60))
    room: Mapped[str] = mapped_column(String(60))
    interviewer: Mapped[str | None] = mapped_column(String(120))

    round: Mapped[Round] = relationship(back_populates="panels")


class Application(Base, Timestamped):
    __tablename__ = "applications"
    __table_args__ = (UniqueConstraint("drive_id", "student_id"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    drive_id: Mapped[int] = mapped_column(ForeignKey("drives.id", ondelete="CASCADE"), index=True)
    student_id: Mapped[int] = mapped_column(ForeignKey("students.id", ondelete="CASCADE"), index=True)
    resume_id: Mapped[int | None] = mapped_column(ForeignKey("resumes.id", ondelete="SET NULL"))
    status: Mapped[AppStatus] = mapped_column(_enum(AppStatus), default=AppStatus.applied)
    rounds_cleared: Mapped[int] = mapped_column(Integer, default=0)
    rejected_round: Mapped[int | None] = mapped_column(Integer)

    drive: Mapped[Drive] = relationship(back_populates="applications")
    student: Mapped[Student] = relationship(back_populates="applications")
    offer: Mapped[Offer | None] = relationship(back_populates="application", uselist=False)


class Slot(Base):
    __tablename__ = "slots"
    __table_args__ = (UniqueConstraint("round_id", "application_id"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    drive_id: Mapped[int] = mapped_column(ForeignKey("drives.id", ondelete="CASCADE"), index=True)
    round_id: Mapped[int] = mapped_column(ForeignKey("rounds.id", ondelete="CASCADE"))
    panel_id: Mapped[int] = mapped_column(ForeignKey("panels.id", ondelete="CASCADE"), index=True)
    application_id: Mapped[int] = mapped_column(ForeignKey("applications.id", ondelete="CASCADE"))
    start_at: Mapped[datetime] = mapped_column(UTCDateTime)
    end_at: Mapped[datetime] = mapped_column(UTCDateTime)
    status: Mapped[SlotStatus] = mapped_column(_enum(SlotStatus), default=SlotStatus.scheduled)
    checked_in_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    actual_start: Mapped[datetime | None] = mapped_column(UTCDateTime)
    actual_end: Mapped[datetime | None] = mapped_column(UTCDateTime)
    result: Mapped[str | None] = mapped_column(String(8))  # pass | fail

    application: Mapped[Application] = relationship()
    panel: Mapped[Panel] = relationship()
    round: Mapped[Round] = relationship()


class ReplanProposal(Base):
    __tablename__ = "replan_proposals"
    id: Mapped[int] = mapped_column(primary_key=True)
    drive_id: Mapped[int] = mapped_column(ForeignKey("drives.id", ondelete="CASCADE"), index=True)
    reason: Mapped[str] = mapped_column(String(300))
    changes: Mapped[list[dict[str, Any]]] = mapped_column(JSON)
    metrics: Mapped[dict[str, Any]] = mapped_column(JSON)
    status: Mapped[ProposalStatus] = mapped_column(_enum(ProposalStatus), default=ProposalStatus.pending)
    decided_by: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    decided_at: Mapped[datetime | None] = mapped_column(UTCDateTime)


class Offer(Base):
    __tablename__ = "offers"
    id: Mapped[int] = mapped_column(primary_key=True)
    application_id: Mapped[int] = mapped_column(
        ForeignKey("applications.id", ondelete="CASCADE"), unique=True
    )
    ctc_lpa: Mapped[float] = mapped_column(Float)
    deadline: Mapped[datetime] = mapped_column(UTCDateTime)
    status: Mapped[OfferStatus] = mapped_column(_enum(OfferStatus), default=OfferStatus.pending)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    decided_at: Mapped[datetime | None] = mapped_column(UTCDateTime)

    application: Mapped[Application] = relationship(back_populates="offer")


class Dispute(Base):
    __tablename__ = "disputes"
    id: Mapped[int] = mapped_column(primary_key=True)
    student_id: Mapped[int] = mapped_column(ForeignKey("students.id", ondelete="CASCADE"), index=True)
    drive_id: Mapped[int] = mapped_column(ForeignKey("drives.id", ondelete="CASCADE"), index=True)
    message: Mapped[str] = mapped_column(Text)
    status: Mapped[DisputeStatus] = mapped_column(_enum(DisputeStatus), default=DisputeStatus.open)
    resolution: Mapped[str | None] = mapped_column(Text)
    resolved_by: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    resolved_at: Mapped[datetime | None] = mapped_column(UTCDateTime)

    student: Mapped[Student] = relationship()
    drive: Mapped[Drive] = relationship()


class Notification(Base):
    __tablename__ = "notifications"
    id: Mapped[int] = mapped_column(primary_key=True)
    student_id: Mapped[int] = mapped_column(ForeignKey("students.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(160))
    body: Mapped[str] = mapped_column(Text)
    link: Mapped[str | None] = mapped_column(String(300))
    read: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class Experience(Base):
    """Structured interview experience shared after a drive; feeds next year's prep insights."""

    __tablename__ = "experiences"
    __table_args__ = (UniqueConstraint("student_id", "drive_id"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    student_id: Mapped[int] = mapped_column(ForeignKey("students.id", ondelete="CASCADE"))
    drive_id: Mapped[int] = mapped_column(ForeignKey("drives.id", ondelete="CASCADE"), index=True)
    difficulty: Mapped[int] = mapped_column(Integer)
    topics: Mapped[list[str]] = mapped_column(JSON, default=list)
    tips: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class Event(Base):
    """Append-only log: live updates, audit trail and analytics all read from here."""

    __tablename__ = "events"
    id: Mapped[int] = mapped_column(primary_key=True)
    ts: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, index=True)
    type: Mapped[str] = mapped_column(String(60), index=True)
    actor: Mapped[str] = mapped_column(String(255))
    entity: Mapped[str | None] = mapped_column(String(40))
    entity_id: Mapped[int | None] = mapped_column(Integer)
    drive_id: Mapped[int | None] = mapped_column(Integer, index=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
