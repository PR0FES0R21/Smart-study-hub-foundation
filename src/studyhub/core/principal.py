"""Authenticated actor and optional school membership shared by API and services."""

from dataclasses import dataclass
from uuid import UUID

from studyhub.models import Membership, School, User


@dataclass
class Principal:
    user: User
    session_id: UUID
    school: School | None = None
    membership: Membership | None = None

    @property
    def school_id(self) -> UUID:
        if self.school is None:
            raise RuntimeError("School context is required")
        return self.school.id
