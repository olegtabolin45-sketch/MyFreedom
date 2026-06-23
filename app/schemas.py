"""Pydantic-схемы валидации запросов."""
import re

from pydantic import BaseModel, EmailStr, Field, field_validator


class RegisterRequest(BaseModel):
    username: str = Field(..., min_length=2, max_length=50)
    email: EmailStr
    password: str

    @field_validator("password")
    @classmethod
    def validate_password_strength(cls, v: str) -> str:
        if len(v) < 8:
            raise ValueError("Пароль должен быть не менее 8 символов.")
        if not re.search(r"[A-ZА-Я]", v):
            raise ValueError("Пароль должен содержать хотя бы одну заглавную букву.")
        if not re.search(r"[a-zа-я]", v):
            raise ValueError("Пароль должен содержать хотя бы одну строчную букву.")
        if not re.search(r"[0-9]", v):
            raise ValueError("Пароль должен содержать хотя бы одну цифру.")
        if not re.search(r"[@$!%*?&_#№\-+=/\\|()\[\]{}]", v):
            raise ValueError(
                "Пароль должен содержать хотя бы один специальный символ (например, @, $, !, %)."
            )
        return v


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class OnboardingRequest(BaseModel):
    currency: str
    initial_capital: float = Field(..., gt=0)
    monthly_deposit: float = Field(..., ge=0)
    target_income: float = Field(..., gt=0)
    years_horizon: int = Field(..., gt=0)
    risk_profile: str
