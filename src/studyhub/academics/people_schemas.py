from datetime import UTC, date, datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import EmailStr, Field, StringConstraints, field_validator

from studyhub.academics.schemas import Input, Read
from studyhub.academics.student_schemas import StudentFields, StudentRead


class Revision(Input):
    expected_revision: int = Field(ge=1)


class PersonWrite(Input):
    full_name: str = Field(min_length=1, max_length=120)
    gender: Literal["male", "female"] | None = None
    birth_date: date | None = None
    birth_place: str | None = Field(default=None, min_length=1, max_length=120)
    phone_number: str | None = Field(default=None, pattern=r"^\+?[0-9 ()-]{6,32}$")
    address: str | None = Field(default=None, min_length=1, max_length=1000)

    @field_validator("birth_date")
    @classmethod
    def birth_in_past(cls, value: date | None) -> date | None:
        if value and not date(1900, 1, 1) <= value <= datetime.now(UTC).date():
            raise ValueError("Birth date must be between 1900 and today")
        return value


class AccountRegister(PersonWrite):
    email: EmailStr
    password: Annotated[
        str, StringConstraints(strip_whitespace=False, min_length=12, max_length=128)
    ]


class MemberRead(PersonWrite):
    school_id: UUID
    user_id: UUID
    email: str
    role: Literal["admin", "teacher", "student"]
    membership_active: bool
    account_active: bool
    revision: int


class StudentRegister(AccountRegister, StudentFields):
    pass


class StudentUpdate(PersonWrite, StudentFields, Revision):
    pass


class StudentDetail(StudentRead):
    member: MemberRead


class TeacherFields(Input):
    employee_number: str | None = Field(default=None, min_length=1, max_length=32)
    nip: str | None = Field(default=None, pattern=r"^[0-9]{18}$")
    employment_type: Literal["permanent", "contract", "honorary"] | None = None
    qualification: str | None = Field(default=None, min_length=1, max_length=120)
    joined_on: date | None = None


class TeacherCreate(TeacherFields):
    user_id: UUID


class TeacherRegister(AccountRegister, TeacherFields):
    pass


class TeacherUpdate(PersonWrite, TeacherFields, Revision):
    pass


class TeacherRead(Read, TeacherFields):
    user_id: UUID
    member: MemberRead


class AdminUpdate(PersonWrite, Revision):
    pass
