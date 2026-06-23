"""Отдача статических файлов фронтенда с того же origin, что и API."""
import os

from fastapi import APIRouter
from fastapi.responses import FileResponse

from app import config

router = APIRouter()


@router.get("/")
async def serve_index():
    return FileResponse(os.path.join(config.PROJECT_ROOT, "index.html"))


@router.get("/dashboard.html")
async def serve_dashboard():
    return FileResponse(os.path.join(config.PROJECT_ROOT, "dashboard.html"))
