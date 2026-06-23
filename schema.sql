-- Схема базы данных приложения (PostgreSQL).
-- Применение:
--   docker exec -i aeterna-db psql -U aeterna -d aeterna < schema.sql

CREATE TABLE IF NOT EXISTS users (
    id            BIGSERIAL PRIMARY KEY,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    username      VARCHAR(50) NOT NULL,
    email         VARCHAR(255) NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    is_onboarded  BOOLEAN NOT NULL DEFAULT false
);

CREATE TABLE IF NOT EXISTS user_goals (
    id                 BIGSERIAL PRIMARY KEY,
    email              VARCHAR(255) NOT NULL UNIQUE REFERENCES users(email) ON DELETE CASCADE,
    currency           VARCHAR(10) NOT NULL,
    initial_capital    DOUBLE PRECISION NOT NULL,
    monthly_deposit    DOUBLE PRECISION NOT NULL,
    target_income      DOUBLE PRECISION NOT NULL,
    years_horizon      INTEGER NOT NULL,
    risk_profile       VARCHAR(50) NOT NULL,
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);
