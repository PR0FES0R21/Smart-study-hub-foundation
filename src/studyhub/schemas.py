from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field, StringConstraints, field_validator


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Login(Input):
    email: EmailStr
    password: Annotated[
        str, StringConstraints(strip_whitespace=False, min_length=1, max_length=128)
    ]


class Refresh(Input):
    refresh_token: str = Field(min_length=32, max_length=200)


class Tokens(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int


class UserCreate(Input):
    email: EmailStr
    full_name: str = Field(min_length=1, max_length=120)
    password: Annotated[
        str, StringConstraints(strip_whitespace=False, min_length=12, max_length=128)
    ]
    role: Literal["teacher", "student"] = "student"


class UserRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    email: str
    full_name: str
    is_platform_admin: bool
    is_active: bool


class SubjectWrite(Input):
    code: str = Field(min_length=1, max_length=24, pattern=r"^[A-Za-z0-9_-]+$")
    name: str = Field(min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=1000)

    @field_validator("code")
    @classmethod
    def normalize_code(cls, value: str) -> str:
        return value.upper()


class SchoolUserRead(UserRead):
    role: Literal["admin", "teacher", "student"]
    membership_active: bool


class SubjectRead(SubjectWrite):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    school_id: UUID
    created_at: datetime


class JobRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    school_id: UUID
    status: str
    result: dict | None
    created_at: datetime
    completed_at: datetime | None


class SchoolCreate(Input):
    code: str = Field(min_length=1, max_length=24, pattern=r"^[A-Z0-9_-]+$")
    name: str = Field(min_length=1, max_length=120)
    level: Literal["SMP", "SMA", "SMK"]


class SchoolRead(SchoolCreate):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    is_active: bool


class MembershipCreate(Input):
    user_id: UUID
    role: Literal["admin", "teacher", "student"]


class ProgramCreate(Input):
    code: str = Field(min_length=1, max_length=24, pattern=r"^[A-Z0-9_-]+$")
    name: str = Field(min_length=1, max_length=120)


class ProgramRead(ProgramCreate):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    school_id: UUID


class ClassCreate(Input):
    name: str = Field(min_length=1, max_length=120)
    grade_level: int = Field(ge=7, le=13)
    program_id: UUID | None = None


class ClassRead(ClassCreate):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    school_id: UUID
