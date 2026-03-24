import os

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.responses import RedirectResponse

from app.database import engine, Base
from app.config import LOCODE_CSV_PATH
import app.models  # noqa: F401 — ensure models are registered before create_all

app = FastAPI(title="console-check", version="1.0.0")

# Create tables on startup
Base.metadata.create_all(bind=engine)

# Ensure upload directory exists
os.makedirs("uploads", exist_ok=True)

# Mount static files and templates
app.mount("/static", StaticFiles(directory="app/static"), name="static")
templates = Jinja2Templates(directory="app/templates")

# Import and include routers
from app.routers import sites, import_sites, verify, dashboard  # noqa: E402

app.include_router(sites.router)
app.include_router(import_sites.router)
app.include_router(verify.router)
app.include_router(dashboard.router)


# HTML page routes
@app.api_route("/", methods=["GET", "HEAD"])
async def root():
    return RedirectResponse("/dashboard")


@app.get("/import")
async def import_page(request: Request):
    return templates.TemplateResponse(request, "import.html")


@app.get("/sites")
async def sites_page(request: Request):
    return templates.TemplateResponse(request, "sites.html")


@app.get("/sites/{site_id}/verify")
async def verify_page(request: Request, site_id: str):
    return templates.TemplateResponse(request, "verify.html", {"site_id": site_id})


@app.get("/dashboard")
async def dashboard_page(request: Request):
    return templates.TemplateResponse(request, "dashboard.html")
