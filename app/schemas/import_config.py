from pydantic import BaseModel


class ColumnMapping(BaseModel):
    site_id: int          # Column index (0-based)
    city: int
    state: int
    bank_name: int
    site_type: int        # "single" or "ha"
    old_primary_hostname: int
    old_secondary_hostname: int | None = None
    new_primary_hostname: int
    new_secondary_hostname: int | None = None
    full_address: int | None = None


class ImportPreview(BaseModel):
    headers: list[str]
    sample_rows: list[list[str]]
    total_rows: int


class UnresolvedSite(BaseModel):
    site_id: str
    city: str
    state: str
    suggestions: list[dict]


class ImportResult(BaseModel):
    total: int
    imported: int
    resolved: int
    unresolved: int
    unresolved_sites: list[UnresolvedSite]
    errors: list[str]
