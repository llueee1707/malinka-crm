from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.api.web import router as web_router
from app.core.config import BASE_DIR
from app.core.config import get_settings
from app.db.base import Base
from app.db.session import SessionLocal
from app.db.session import engine
from app.models import entities  # noqa: F401
from app.services.bootstrap import bootstrap_defaults


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(bind=engine)
    with SessionLocal() as db:
        bootstrap_defaults(db)
    yield


settings = get_settings()
app = FastAPI(title=settings.app_name, lifespan=lifespan)
app.mount("/static", StaticFiles(directory=BASE_DIR / "app" / "static"), name="static")
app.include_router(web_router)
