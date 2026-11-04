from sqlalchemy import select

from app.core.config import settings
from app.core.db import Session


def health():
    with Session() as session:
        session.execute(select(1))
    return {"status": "ok"}


def config():
    return {
        "modes": ["scripted", "model"] if settings.model_enabled else ["scripted"],
        "demo_urls": {
            "faulty": settings.demo_url + "/faulty/",
            "corrected": settings.demo_url + "/corrected/",
        },
        "max_pages": 5,
        "access": "local operator only",
    }
