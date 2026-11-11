from uuid import UUID

from pydantic import Field

from studyhub.academics.people_schemas import Revision
from studyhub.academics.schemas import Input, PeriodCreate, Read
from studyhub.schemas import ClassCreate, ProgramCreate


class YearWrite(Input):
    year_start: int = Field(ge=2000, le=2200)
    description: str | None = Field(default=None, max_length=1000)


class YearRead(Read, YearWrite):
    label: str
    revision: int


class YearUpdate(YearWrite, Revision):
    pass


class PeriodUpdate(PeriodCreate, Revision):
    pass


class ClassUpdate(ClassCreate, Revision):
    pass


class ProgramUpdate(ProgramCreate, Revision):
    pass


class OfferingFields(Input):
    homeroom_teacher_id: UUID | None = None
    room: str | None = Field(default=None, min_length=1, max_length=64)


class OfferingCreate(OfferingFields):
    period_id: UUID
    class_id: UUID


class OfferingUpdate(OfferingFields, Revision):
    pass


class OfferingRead(Read, OfferingCreate):
    revision: int
