import os
import shutil

from fastapi import APIRouter, Depends, UploadFile, File, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.config import LOCODE_CSV_PATH, UPLOAD_DIR
from app.schemas.import_config import ColumnMapping
from app.services.unlocode import UnlocodeService
from app.services.importer import SiteImporter
from app.models.site import Site

router = APIRouter(prefix="/api/import", tags=["import"])

# Singleton UNLOCODE service
_unlocode_service: UnlocodeService | None = None


def get_unlocode_service() -> UnlocodeService:
    global _unlocode_service
    if _unlocode_service is None:
        _unlocode_service = UnlocodeService(LOCODE_CSV_PATH)
    return _unlocode_service


@router.post("/upload")
async def upload_file(file: UploadFile = File(...)):
    """Upload Excel/CSV file, return preview (headers + sample rows)."""
    if not file.filename:
        raise HTTPException(status_code=400, detail="No file provided")

    ext = os.path.splitext(file.filename)[1].lower()
    if ext not in ('.xlsx', '.xls', '.csv'):
        raise HTTPException(status_code=400, detail="Only .xlsx, .xls, and .csv files are supported")

    os.makedirs(UPLOAD_DIR, exist_ok=True)
    file_path = os.path.join(UPLOAD_DIR, file.filename)

    with open(file_path, "wb") as f:
        shutil.copyfileobj(file.file, f)

    svc = get_unlocode_service()
    importer = SiteImporter(svc, None)  # db not needed for preview
    preview = importer.preview(file_path)

    return {
        "file_path": file_path,
        "headers": preview.headers,
        "sample_rows": preview.sample_rows,
        "total_rows": preview.total_rows,
    }


class ExecuteRequest(BaseModel):
    file_path: str
    column_map: ColumnMapping


@router.post("/execute")
async def execute_import(req: ExecuteRequest, db: Session = Depends(get_db)):
    """Execute import with column mapping, return results."""
    if not os.path.exists(req.file_path):
        raise HTTPException(status_code=400, detail="File not found. Please upload again.")

    svc = get_unlocode_service()
    importer = SiteImporter(svc, db)
    result = importer.execute(req.file_path, req.column_map)
    return result


@router.get("/unresolved")
async def get_unresolved(db: Session = Depends(get_db)):
    """Get list of sites with unresolved UNLOCODEs."""
    sites = db.query(Site).filter(Site.unlocode_resolved == False).all()  # noqa: E712
    return [
        {
            "site_id": s.id,
            "city": s.city,
            "state": s.state,
            "bank_name": s.bank_name,
            "unlocode": s.unlocode,
        }
        for s in sites
    ]


class ResolveRequest(BaseModel):
    unlocode: str


@router.post("/resolve/{site_id}")
async def resolve_site(site_id: str, req: ResolveRequest, db: Session = Depends(get_db)):
    """Manually set UNLOCODE for a site."""
    site = db.query(Site).filter(Site.id == site_id).first()
    if not site:
        raise HTTPException(status_code=404, detail="Site not found")

    site.unlocode = req.unlocode.upper()
    site.unlocode_resolved = True
    site.cp_search_key = f"US{req.unlocode.upper()}"
    db.commit()

    return {"status": "ok", "site_id": site_id, "cp_search_key": site.cp_search_key}
