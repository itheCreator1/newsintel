from fastapi import FastAPI
from fastapi.middleware.trustedhost import TrustedHostMiddleware

from app.api.health import router as health_router
from app.articles.routes import router as articles_router
from app.auth.routes import router as auth_router
from app.core.config import Settings, get_settings
from app.feeds.routes import router as feeds_router
from app.search.routes import router as search_router


def create_app(settings: Settings | None = None) -> FastAPI:
    config = settings or get_settings()
    app = FastAPI(title="NewsIntel API", version="0.1.0")
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=config.allowed_hosts)
    app.include_router(health_router, prefix="/api/v1")
    app.include_router(auth_router, prefix="/api/v1")
    app.include_router(feeds_router, prefix="/api/v1")
    app.include_router(articles_router, prefix="/api/v1")
    app.include_router(search_router, prefix="/api/v1")
    return app


app = create_app()
