"""FastAPI application entry point."""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.config import settings as app_settings
from app.database import init_db, seed_db_if_empty
from app.startup_checks import log_pricing_db_status
from app.api.routes import policies, templates, seed, filters, checks, settings, onboarding, action_helpers, cur, inventory, scans, mcp, recommendations, rightsizer, s3_optimizer

# Validate required local artifacts
log_pricing_db_status()

# Initialize database
init_db()

# Seed database with default policies if empty (first run)
seed_db_if_empty()


def _prime_cur_pricing_preference() -> None:
    """Load the stored CUR pricing choice so the first scan honours it.

    Best effort by design. This is an optional feature that is off by default,
    so nothing here may prevent the application from starting. If the
    preference cannot be read, the environment default applies.
    """
    import logging

    try:
        from app.database import SessionLocal
        from app.services.cur_cost_service import load_cur_pricing_enabled

        db = SessionLocal()
        try:
            load_cur_pricing_enabled(db)
        finally:
            db.close()
    except Exception as exc:  # noqa: BLE001
        logging.getLogger(__name__).warning(
            "Could not load the cost-data pricing preference; using the default. %s", exc
        )


_prime_cur_pricing_preference()


def _sync_host_aws_profiles() -> None:
    """Make the host's ~/.aws/config profiles visible when AWS_CONFIG_FILE is redirected.

    Best effort: profile discovery is an onboarding convenience, so a failure
    here must not stop the API from starting.
    """
    import logging

    try:
        from app.services.iam_onboarding_service import sync_host_aws_profiles

        sync_host_aws_profiles()
    except Exception as exc:  # noqa: BLE001
        logging.getLogger(__name__).warning("Could not sync host AWS profiles. %s", exc)


_sync_host_aws_profiles()

# Create FastAPI app
app = FastAPI(
    title="MaxOps API",
    description="FinOps Cost Optimization Platform API",
    version="0.1.0"
)

# Configure CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=app_settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include routers
app.include_router(checks.router, prefix="/api/v1", tags=["checks"])
app.include_router(action_helpers.router, prefix="/api/v1", tags=["action-helpers"])
app.include_router(inventory.router, prefix="/api/v1")
app.include_router(recommendations.router, prefix="/api/v1")
app.include_router(rightsizer.router, prefix="/api/v1")
app.include_router(scans.router, prefix="/api/v1", tags=["scans"])
app.include_router(policies.router, prefix="/api/v1")
app.include_router(templates.router, prefix="/api/v1")
if app_settings.debug:
    app.include_router(seed.router, prefix="/api/v1")
app.include_router(filters.router, prefix="/api/v1")
app.include_router(settings.router, prefix="/api/v1")
app.include_router(onboarding.router, prefix="/api/v1")
app.include_router(mcp.router, prefix="/api/v1")
app.include_router(cur.router, prefix="/api/v1")
app.include_router(s3_optimizer.router, prefix="/api/v1")


@app.get("/")
def root():
    """Root endpoint."""
    return {
        "message": "MaxOps API",
        "version": "0.1.0",
        "docs": "/docs"
    }


@app.get("/health")
def health():
    """Health check endpoint."""
    return {"status": "healthy"}
