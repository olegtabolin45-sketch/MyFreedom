"""Pydantic-схемы валидации запросов.

Принципы: запрещаем лишние поля (`extra="forbid"` — защита от подмешивания
неожиданных полей), обрезаем пробелы по краям, ограничиваем длины и диапазоны,
для перечислимых значений используем whitelist (Literal).
"""

import re

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

# bcrypt не принимает пароли длиннее 72 байт (в utf-8 кириллица — 2 байта/символ)
MAX_PASSWORD_BYTES = 72

# Допустимые значения онбординга
ALLOWED_CURRENCIES = ("RUB", "USD", "EUR", "GBP", "CNY", "JPY", "CHF")
ALLOWED_RISK_PROFILES = ("conservative", "moderate", "aggressive")
# Страны проживания (для поправки на инфляцию). Должны совпадать с фронтендом.
ALLOWED_COUNTRIES = (
    "RU",
    "US",
    "DE",
    "GB",
    "CH",
    "AE",
    "TR",
    "KZ",
    "GE",
    "TH",
    "ES",
    "CY",
    "RS",
    "ME",
    "PT",
)
MAX_COUNTRIES = 3


class _StrictModel(BaseModel):
    """Базовая модель: режет пробелы по краям строк и запрещает лишние поля."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")


class RegisterRequest(_StrictModel):
    username: str = Field(..., min_length=2, max_length=50)
    email: EmailStr = Field(..., max_length=255)
    password: str = Field(..., min_length=8, max_length=128)

    @field_validator("username")
    @classmethod
    def validate_username(cls, v: str) -> str:
        # Буквы (лат/кириллица), цифры, пробел, дефис, подчёркивание, точка
        if not re.fullmatch(r"[\w .\-А-Яа-яЁё]+", v):
            raise ValueError(
                "Имя может содержать только буквы, цифры, пробел, дефис, точку и подчёркивание."
            )
        return v

    @field_validator("password")
    @classmethod
    def validate_password_strength(cls, v: str) -> str:
        if len(v.encode("utf-8")) > MAX_PASSWORD_BYTES:
            raise ValueError(
                "Пароль слишком длинный (максимум 72 байта; кириллица считается за 2 символа)."
            )
        if not re.search(r"[A-ZА-ЯЁ]", v):
            raise ValueError("Пароль должен содержать хотя бы одну заглавную букву.")
        if not re.search(r"[a-zа-яё]", v):
            raise ValueError("Пароль должен содержать хотя бы одну строчную букву.")
        if not re.search(r"[0-9]", v):
            raise ValueError("Пароль должен содержать хотя бы одну цифру.")
        if not re.search(r"[@$!%*?&_#№\-+=/\\|()\[\]{}]", v):
            raise ValueError(
                "Пароль должен содержать хотя бы один специальный символ (например, @, $, !, %)."
            )
        return v


class PortfolioCreate(_StrictModel):
    name: str = Field(..., min_length=1, max_length=120)
    description: str = Field(default="", max_length=500)
    currency: str = Field(default="RUB")
    kind: str = Field(default="broker")  # broker | virtual
    broker_commission: float | None = Field(default=None, ge=0, le=100)

    @field_validator("currency")
    @classmethod
    def validate_currency(cls, v: str) -> str:
        code = (v or "RUB").upper()
        return code if code in ALLOWED_CURRENCIES else "RUB"

    @field_validator("kind")
    @classmethod
    def validate_kind(cls, v: str) -> str:
        return v if v in ("broker", "virtual") else "broker"


class TbankConnect(_StrictModel):
    # Личный read-only токен T-Bank Invest API
    token: str = Field(..., min_length=10, max_length=200)


class LoginRequest(_StrictModel):
    email: EmailStr = Field(..., max_length=255)
    # Лимит длины — защита от гигантских payload'ов; правила сложности тут не нужны
    password: str = Field(..., min_length=1, max_length=128)
    # Код 2FA (если у пользователя включена двухфакторная аутентификация)
    totp_code: str | None = Field(default=None, min_length=6, max_length=10)


class TwoFactorCodeRequest(_StrictModel):
    code: str = Field(..., min_length=6, max_length=10)


class RefreshRequest(_StrictModel):
    refresh_token: str = Field(..., min_length=1, max_length=512)


class LogoutRequest(_StrictModel):
    refresh_token: str = Field(..., min_length=1, max_length=512)
    # Необязательно: текущий access-токен — попадёт в blacklist до истечения
    access_token: str | None = Field(default=None, max_length=2048)


class OnboardingRequest(_StrictModel):
    currency: str
    initial_capital: float = Field(..., ge=0, le=1e15)
    monthly_deposit: float = Field(..., ge=0, le=1e15)
    target_income: float = Field(..., gt=0, le=1e15)
    years_horizon: int = Field(..., gt=0, le=120)
    risk_profile: str
    # Страны, где пользователь планирует жить (для поправки на инфляцию)
    countries: list[str] = Field(..., min_length=1, max_length=MAX_COUNTRIES)

    @field_validator("countries")
    @classmethod
    def validate_countries(cls, v: list[str]) -> list[str]:
        result = []
        for item in v:
            code = item.strip().upper()
            if code not in ALLOWED_COUNTRIES:
                raise ValueError(f"Неизвестный код страны: {item}.")
            if code not in result:
                result.append(code)
        if not result:
            raise ValueError("Выберите хотя бы одну страну.")
        return result

    @field_validator("currency")
    @classmethod
    def validate_currency(cls, v: str) -> str:
        code = v.upper()
        if code not in ALLOWED_CURRENCIES:
            raise ValueError(f"Валюта должна быть одной из: {', '.join(ALLOWED_CURRENCIES)}.")
        return code

    @field_validator("risk_profile")
    @classmethod
    def validate_risk_profile(cls, v: str) -> str:
        profile = v.lower()
        if profile not in ALLOWED_RISK_PROFILES:
            raise ValueError(
                f"Риск-профиль должен быть одним из: {', '.join(ALLOWED_RISK_PROFILES)}."
            )
        return profile
