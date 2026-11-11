from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from studyhub.academics.people_schemas import (
    PersonWrite,
    StudentRegister,
    StudentUpdate,
    TeacherRegister,
)


@pytest.mark.parametrize(
    "extra",
    [
        {"email": "hijack@example.org"},
        {"password": "hijacked-password"},
        {"is_platform_admin": True},
        {"role": "admin"},
        {"status": "graduated"},
    ],
)
def test_school_updates_cannot_edit_authentication_or_progression(extra):
    with pytest.raises(ValidationError):
        StudentUpdate(full_name="Student", graduation_grade=12, expected_revision=1, **extra)


def test_identifiers_keep_leading_zeroes_and_sensitive_fields_are_bounded():
    common = {"full_name": "Valid", "email": "member@example.org", "password": "good-password-123"}
    assert StudentRegister(**common, graduation_grade=12, nisn="0012345678").nisn == "0012345678"
    assert TeacherRegister(**common, nip="001234567890123456").nip.startswith("00")
    for value in ("123", "123456789a", "１２３４５６７８９０"):
        with pytest.raises(ValidationError):
            StudentRegister(**common, graduation_grade=12, nisn=value)
    for data in (
        {"phone_number": "javascript:bad"},
        {"address": "x" * 1001},
        {"birth_date": (datetime.now(UTC) + timedelta(days=1)).date()},
        {"birth_date": "1899-12-31"},
    ):
        with pytest.raises(ValidationError):
            PersonWrite(full_name="Valid", **data)
