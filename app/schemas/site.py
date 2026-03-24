from pydantic import BaseModel
from datetime import datetime


class DeviceOut(BaseModel):
    id: int
    port: int
    role: str
    expected_hostname: str

    model_config = {"from_attributes": True}


class VerificationOut(BaseModel):
    id: int
    port: int
    expected_hostname: str
    found_hostname: str | None
    verdict: str
    swap_details: str | None
    engineer: str | None
    timestamp: datetime | None

    model_config = {"from_attributes": True}


class SiteListItem(BaseModel):
    id: str
    city: str
    state: str
    bank_name: str
    site_type: str
    unlocode: str | None
    cp_search_key: str | None
    status: str

    model_config = {"from_attributes": True}


class SiteDetail(BaseModel):
    id: str
    city: str
    state: str
    bank_name: str
    full_address: str | None
    site_type: str
    unlocode: str | None
    unlocode_resolved: bool
    cp_search_key: str | None
    status: str
    devices: list[DeviceOut]
    verifications: list[VerificationOut]

    model_config = {"from_attributes": True}


class SiteListResponse(BaseModel):
    items: list[SiteListItem]
    total: int
    page: int
    per_page: int
