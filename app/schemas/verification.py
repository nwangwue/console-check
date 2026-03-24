"""Verification data models."""

from pydantic import BaseModel


class VerifyResult(BaseModel):
    port: int
    expected_hostname: str
    found_hostname: str | None = None
    verdict: str  # match, mismatch, swapped, no_response, unknown
    swap_details: str | None = None
    confidence: str = "none"
