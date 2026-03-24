from pydantic import BaseModel


class VerifyRequest(BaseModel):
    raw_output: str
    engineer: str | None = None


class VerifyResult(BaseModel):
    port: int
    expected_hostname: str
    found_hostname: str | None
    verdict: str  # match, mismatch, swapped, no_response, unknown
    swap_details: str | None = None
    confidence: str = "none"


class AcceptSwapRequest(BaseModel):
    port_a: int
    port_b: int


class PortStatus(BaseModel):
    port: int
    role: str
    expected_hostname: str
    found_hostname: str | None = None
    verdict: str | None = None
    swap_details: str | None = None
    timestamp: str | None = None


class SiteVerificationStatus(BaseModel):
    site_id: str
    status: str
    ports: list[PortStatus]
