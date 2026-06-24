"""Окружение Alembic. URL к БД берётся из app.config (переменные окружения)."""

from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool

from alembic import context
from app import config as app_config

# Конфиг из alembic.ini (для логирования)
config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)


def _database_url() -> str:
    # SQLAlchemy-диалект поверх драйвера pg8000 (тот же, что в приложении)
    return (
        f"postgresql+pg8000://{app_config.DB_USER}:{app_config.DB_PASSWORD}"
        f"@{app_config.DB_HOST}:{app_config.DB_PORT}/{app_config.DB_NAME}"
    )


# Миграции пишутся явно (op.create_table и т.п.), автогенерация из моделей не используется
target_metadata = None


def run_migrations_offline() -> None:
    context.configure(
        url=_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    section = config.get_section(config.config_ini_section) or {}
    section["sqlalchemy.url"] = _database_url()
    connectable = engine_from_config(
        section,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
