import logging
import time

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from sqlalchemy import text
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.config import settings
from app.database import Base, SessionLocal, engine
from app.limiter import limiter
from app.routers import admin, auth, carbon, dashboard, energy, goals, insights, notifications, reports, team, waste, oauth, ml, ingest

# Structured logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("greenpulse")

# Create database tables
Base.metadata.create_all(bind=engine)

app = FastAPI(
    title=settings.APP_NAME,
    version=settings.VERSION,
    debug=settings.DEBUG,
    docs_url="/api/docs",
    redoc_url="/api/redoc",
    openapi_url="/api/openapi.json",
)

# ── Rate limiter ──────────────────────────────────────────────────────────────
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)


# ── Exception handlers ────────────────────────────────────────────────────────
@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": {"message": exc.detail, "code": exc.status_code}},
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={
            "error": {
                "message": "Validation error",
                "code": 422,
                "details": exc.errors(),
            }
        },
    )


@app.exception_handler(Exception)
async def generic_exception_handler(request: Request, exc: Exception):
    logger.error(
        "Unhandled exception on %s %s: %s",
        request.method,
        request.url.path,
        exc,
        exc_info=True,
    )
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={"error": {"message": "Internal server error", "code": 500}},
    )


# ── Request logging middleware ────────────────────────────────────────────────
@app.middleware("http")
async def log_requests(request: Request, call_next):
    start = time.perf_counter()
    response = await call_next(request)
    ms = (time.perf_counter() - start) * 1000
    logger.info(
        "%s %s | %d | %.1fms",
        request.method,
        request.url.path,
        response.status_code,
        ms,
    )
    return response


# ── CORS ──────────────────────────────────────────────────────────────────────
allowed_origins = [o.strip() for o in settings.ALLOWED_ORIGINS.split(",")]
app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    allow_headers=["Authorization", "Content-Type", "Accept", "X-API-Key"],
)

# ── Versioned routers ─────────────────────────────────────────────────────────
API_V1 = "/api/v1"
app.include_router(auth.router, prefix=API_V1)
app.include_router(admin.router, prefix=API_V1)
app.include_router(dashboard.router, prefix=API_V1)
app.include_router(energy.router, prefix=API_V1)
app.include_router(waste.router, prefix=API_V1)
app.include_router(insights.router, prefix=API_V1)
app.include_router(reports.router, prefix=API_V1)
app.include_router(notifications.router, prefix=API_V1)
app.include_router(oauth.router, prefix=API_V1)
app.include_router(ml.router,        prefix=API_V1)
app.include_router(ml.public_router,  prefix=API_V1)
app.include_router(ingest.router,     prefix=API_V1)
app.include_router(carbon.router,     prefix=API_V1)
app.include_router(goals.router,      prefix=API_V1)
app.include_router(team.router,       prefix=API_V1)


# ── ML scheduler (auto-retraining) ────────────────────────────────────────────
@app.on_event("startup")
async def start_ml_scheduler():
    from app.ml.scheduler import start_scheduler
    start_scheduler()


@app.on_event("shutdown")
async def stop_ml_scheduler():
    from app.ml.scheduler import stop_scheduler
    stop_scheduler()


# ── Root ──────────────────────────────────────────────────────────────────────
@app.get("/")
def root():
    return {
        "message": "Welcome to GreenPulse API",
        "version": settings.VERSION,
        "docs": "/api/docs",
        "status": "online",
    }


# ── Health check ──────────────────────────────────────────────────────────────
@app.get("/health")
def health_check():
    db_status = "healthy"
    db = SessionLocal()
    try:
        db.execute(text("SELECT 1"))
    except Exception:
        db_status = "unhealthy"
    finally:
        db.close()

    overall = "healthy" if db_status == "healthy" else "degraded"
    return {
        "status": overall,
        "version": settings.VERSION,
        "services": {"database": db_status},
    }
