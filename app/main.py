import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from app.database import init_db
from app.routes.admin import router as admin_router
from app.routes.auth import router as auth_router
from app.routes.mail import router as mail_router
from app.config import get_settings

logging.basicConfig(level=logging.INFO)
settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    logging.info(f"🚀 {settings.APP_NAME} started")
    yield
    logging.info(f"👋 {settings.APP_NAME} shutting down")


app = FastAPI(
    title=settings.APP_NAME,
    docs_url=None,  # hide swagger in production
    redoc_url=None,
    lifespan=lifespan,
)

# API Routes
app.include_router(admin_router)
app.include_router(auth_router)
app.include_router(mail_router)


# Health check — lightweight, no DB needed
@app.get("/health")
async def health():
    return {"status": "ok"}


# Static files
app.mount("/static", StaticFiles(directory="static"), name="static")


# SPA routes — serve index.html for all non-API routes
@app.get("/")
async def serve_root():
    return FileResponse("static/index.html")


@app.get("/admin")
async def serve_admin():
    return FileResponse("static/admin.html")


@app.get("/inbox")
async def serve_inbox():
    return FileResponse("static/index.html")


@app.get("/{path:path}")
async def catch_all(path: str):
    # Don't catch API routes
    if path.startswith("api/"):
        return {"detail": "Not found"}
    # Try to serve static file first
    import os
    static_path = f"static/{path}"
    if os.path.isfile(static_path):
        return FileResponse(static_path)
    return FileResponse("static/index.html")
