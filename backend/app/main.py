from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import settings
from app.v1.api import api_router

app = FastAPI(title="Arsune", docs_url="/api/docs")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.ui_origin],
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)
app.include_router(api_router)
