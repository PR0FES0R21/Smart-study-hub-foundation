import asyncio
import hmac
import logging
import time
import uuid
from contextlib import AsyncExitStack, asynccontextmanager

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from redis.asyncio import Redis
from redis.exceptions import RedisError
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from starlette.middleware.trustedhost import TrustedHostMiddleware

from studyhub.academics.api import router as academic_router
from studyhub.api import routes, schools, sso, websocket
from studyhub.api.middleware import BodyLimitMiddleware
from studyhub.assessments.api import router as assessment_router
from studyhub.core.config import Settings, get_settings
from studyhub.core.context import actor_id_context, request_id_context, school_id_context
from studyhub.core.errors import AuthenticationError, ResourceNotFound
from studyhub.core.logging import configure_logging
from studyhub.infrastructure.cache import Cache
from studyhub.infrastructure.database import create_database
from studyhub.infrastructure.events import EventHub
from studyhub.infrastructure.integrations import IntegrationClient
from studyhub.infrastructure.metrics import Metrics
from studyhub.infrastructure.redis import create_redis
from studyhub.services.oidc import OIDC

logger = logging.getLogger(__name__)


def create_app(settings: Settings | None = None) -> FastAPI:
    configure_logging()
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        async with AsyncExitStack() as stack:
            engine, sessions = create_database(settings)
            stack.push_async_callback(engine.dispose)
            app.state.engine, app.state.sessions, app.state.settings = engine, sessions, settings
            if settings.environment == "production":
                async with engine.connect() as connection:
                    privileged = await connection.scalar(
                        text(
                            "SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname = current_user"
                        )
                    )
                    if privileged:
                        raise RuntimeError(
                            "Runtime database role must not bypass row level security"
                        )
            for name, url in (
                ("cache_redis", settings.cache_url),
                ("control", settings.control_url),
                ("broker", settings.broker_url),
            ):
                client = create_redis(url.get_secret_value())
                setattr(app.state, name, client)
                stack.push_async_callback(client.aclose)
            # PubSub owns its connection and blocks while idle; it must not inherit a command read timeout.
            subscriber = Redis.from_url(
                settings.control_url.get_secret_value(),
                decode_responses=True,
                max_connections=2,
                socket_connect_timeout=2,
                health_check_interval=15,
            )
            stack.push_async_callback(subscriber.aclose)
            app.state.cache = Cache(app.state.cache_redis, settings.namespace)
            app.state.hub = EventHub(subscriber, settings.namespace, app.state.cache)
            app.state.hub.start()
            stack.push_async_callback(app.state.hub.stop)
            headers = (
                {"Authorization": f"Bearer {settings.integration_token.get_secret_value()}"}
                if settings.integration_token
                else {}
            )
            outbound = await stack.enter_async_context(
                httpx.AsyncClient(
                    base_url=settings.integration_base_url or "https://integration.invalid/",
                    headers=headers,
                    timeout=httpx.Timeout(5, connect=2),
                    limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
                    follow_redirects=False,
                )
            )
            app.state.integration = IntegrationClient(outbound)
            oidc_http = await stack.enter_async_context(
                httpx.AsyncClient(
                    timeout=httpx.Timeout(5, connect=2),
                    follow_redirects=False,
                    limits=httpx.Limits(max_connections=10),
                )
            )
            app.state.oidc = OIDC(settings, oidc_http, app.state.control)
            yield

    app = FastAPI(
        title="Smart Study Hub Foundation",
        version="0.4.0",
        lifespan=lifespan,
        docs_url=None if settings.environment == "production" else "/docs",
        redoc_url=None,
        openapi_url="/api/v1/openapi.json",
    )
    app.state.metrics = Metrics()
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.allowed_hosts)
    app.add_middleware(BodyLimitMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.allowed_origins,
        allow_credentials=False,
        allow_methods=["GET", "POST", "PUT", "DELETE"],
        allow_headers=["Authorization", "Content-Type", "Idempotency-Key", "X-School-ID"],
        expose_headers=["X-Request-ID"],
    )

    @app.middleware("http")
    async def request_metadata(request: Request, call_next):
        request_id = str(uuid.uuid4())
        request.state.request_id = request_id
        context = request_id_context.set(request_id)
        start = time.monotonic()
        status = 500
        try:
            response = await call_next(request)
            status = response.status_code
        except Exception as exc:
            logger.error("request_failed", extra={"error_type": type(exc).__name__})
            raise
        finally:
            route = getattr(request.scope.get("route"), "path", "__unmatched__")
            if route != "/internal/metrics":
                app.state.metrics.observe(request.method, route, status, time.monotonic() - start)
            logger.info(
                "http_request",
                extra={
                    "request_id": request_id,
                    "method": request.method,
                    "path": route,
                    "status": status,
                    "duration_ms": round((time.monotonic() - start) * 1000, 2),
                },
            )
            request_id_context.reset(context)
            actor_id_context.set(None)
            school_id_context.set(None)
        response.headers.update(
            {
                "X-Request-ID": request_id,
                "X-Content-Type-Options": "nosniff",
                "Referrer-Policy": "no-referrer",
                "Cache-Control": "no-store",
            }
        )
        return response

    @app.exception_handler(Exception)
    async def unexpected_error(request: Request, exc: Exception):
        request_id = getattr(request.state, "request_id", str(uuid.uuid4()))
        return JSONResponse(
            status_code=500,
            content={"detail": "Internal server error", "request_id": request_id},
            headers={
                "X-Request-ID": request_id,
                "Cache-Control": "no-store",
                "X-Content-Type-Options": "nosniff",
            },
        )

    @app.get("/internal/metrics", include_in_schema=False)
    async def metrics(request: Request):
        token = settings.metrics_token
        if not token:
            raise HTTPException(404, "Not found")
        expected = "Bearer " + token.get_secret_value()
        if not hmac.compare_digest(
            request.headers.get("authorization", "").encode(), expected.encode()
        ):
            raise HTTPException(401, "Invalid metrics credential")
        try:
            content = await asyncio.wait_for(app.state.metrics.render(app.state), timeout=8)
        except TimeoutError as exc:
            raise HTTPException(503, "Metrics collection timed out") from exc
        return Response(content=content, media_type="text/plain; version=0.0.4; charset=utf-8")

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request: Request, exc: RequestValidationError):
        return JSONResponse(
            status_code=422,
            content={
                "detail": [
                    {"loc": list(e["loc"]), "msg": e["msg"], "type": e["type"]}
                    for e in exc.errors()
                ]
            },
        )  # Pydantic's default error includes the submitted password/token as input.

    @app.exception_handler(IntegrityError)
    async def conflict(request: Request, exc: IntegrityError):
        return JSONResponse(
            status_code=409, content={"detail": "Resource conflicts with an existing record"}
        )

    @app.exception_handler(AuthenticationError)
    async def authentication_error(request: Request, exc: AuthenticationError):
        return JSONResponse(
            status_code=401, content={"detail": str(exc)}, headers={"WWW-Authenticate": "Bearer"}
        )

    @app.exception_handler(ResourceNotFound)
    async def not_found(request: Request, exc: ResourceNotFound):
        return JSONResponse(status_code=404, content={"detail": str(exc)})

    @app.exception_handler(SQLAlchemyError)
    async def database_error(request: Request, exc: SQLAlchemyError):
        logger.error("database_operation_failed", extra={"error_type": type(exc).__name__})
        return JSONResponse(status_code=503, content={"detail": "Database temporarily unavailable"})

    @app.exception_handler(RedisError)
    async def redis_error(request: Request, exc: RedisError):
        return JSONResponse(
            status_code=503, content={"detail": "Control service temporarily unavailable"}
        )

    @app.get("/health/live", tags=["health"])
    async def live():
        return {"status": "alive"}

    @app.get("/health/ready", tags=["health"])
    async def ready():
        async def ping_db():
            async with app.state.engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
                await conn.execute(text("SELECT version_num FROM alembic_version LIMIT 1"))

        checks = await asyncio.gather(
            ping_db(),
            app.state.control.ping(),
            app.state.broker.ping(),
            app.state.cache_redis.ping(),
            return_exceptions=True,
        )
        dependencies = dict(
            zip(
                ("database", "control", "broker", "cache"),
                (not isinstance(c, BaseException) for c in checks),
                strict=True,
            )
        )
        dependencies["subscriber"] = app.state.hub.ready.is_set()
        essential = all(dependencies[k] for k in ("database", "control", "broker", "subscriber"))
        return JSONResponse(
            status_code=200 if essential else 503,
            content={"status": "ready" if essential else "not_ready", "dependencies": dependencies},
        )

    app.include_router(routes.router)
    app.include_router(schools.router)
    app.include_router(academic_router)
    app.include_router(assessment_router)
    app.include_router(sso.router)
    app.include_router(websocket.router)
    return app
