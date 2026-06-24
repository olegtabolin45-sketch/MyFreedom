"""Сборка FastAPI-приложения: middleware и подключение роутеров."""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import config
from app.middleware import SecurityHeadersMiddleware
from app.routers import auth, frontend, two_factor, users

app = FastAPI(title="MyFreedom Core API", version="1.1.0")

# Заголовки безопасности на все ответы
app.add_middleware(SecurityHeadersMiddleware)

app.add_middleware(
    CORSMiddleware,
    allow_origins=config.ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(frontend.router)
app.include_router(auth.router)
app.include_router(users.router)
app.include_router(two_factor.router)
