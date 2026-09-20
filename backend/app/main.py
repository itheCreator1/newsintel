from fastapi import FastAPI
from fastapi.middleware.trustedhost import TrustedHostMiddleware

from app.analytics.routes import router as analytics_router
from app.api.health import router as health_router
from app.articles.routes import router as articles_router
from app.auth.routes import router as auth_router
from app.clustering.routes import router as clustering_router
from app.core.config import Settings, get_settings
from app.entities.routes import router as entities_router
from app.events.routes import router as events_router
from app.feeds.routes import router as feeds_router
from app.graph.routes import router as graph_router
from app.investigations.routes import router as investigations_router
from app.monitors.routes import router as monitors_router
from app.nlp.routes import router as nlp_router
from app.search.routes import router as search_router
from app.sources.routes import router as sources_router


def create_app(settings: Settings | None = None) -> FastAPI:
    config = settings or get_settings()
    app = FastAPI(title="NewsIntel API", version="0.1.0")
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=config.allowed_hosts)
    app.include_router(health_router, prefix="/api/v1")
    app.include_router(auth_router, prefix="/api/v1")
    app.include_router(feeds_router, prefix="/api/v1")
    app.include_router(articles_router, prefix="/api/v1")
    app.include_router(nlp_router, prefix="/api/v1")
    app.include_router(entities_router, prefix="/api/v1")
    app.include_router(clustering_router, prefix="/api/v1")
    app.include_router(events_router, prefix="/api/v1")
    app.include_router(sources_router, prefix="/api/v1")
    app.include_router(search_router, prefix="/api/v1")
    app.include_router(graph_router, prefix="/api/v1")
    app.include_router(investigations_router, prefix="/api/v1")
    app.include_router(monitors_router, prefix="/api/v1")
    app.include_router(analytics_router, prefix="/api/v1")
    return app


app = create_app()
