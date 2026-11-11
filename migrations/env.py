from alembic import context
from sqlalchemy import create_engine, pool

from studyhub import models  # noqa: F401
from studyhub.academics import models as academic_models  # noqa: F401
from studyhub.academics import (
    people_models,  # noqa: F401
    student_models,  # noqa: F401
)
from studyhub.assessments import delivery_models  # noqa: F401
from studyhub.assessments import models as assessment_models  # noqa: F401
from studyhub.core.config import get_settings
from studyhub.infrastructure.database import Base

url = get_settings().sync_migration_url

if context.is_offline_mode():
    context.configure(
        url=url,
        target_metadata=Base.metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()
else:
    engine = create_engine(url, poolclass=pool.NullPool)
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=Base.metadata, compare_type=True)
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()
