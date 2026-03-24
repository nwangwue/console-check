from sqlalchemy import Column, String, Boolean, DateTime
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship

from app.database import Base


class Site(Base):
    __tablename__ = "sites"

    id = Column(String, primary_key=True)  # Site ID from spreadsheet (e.g., "FIS-2847")
    city = Column(String, nullable=False)
    state = Column(String(2), nullable=False)
    bank_name = Column(String, nullable=False)
    full_address = Column(String, nullable=True)
    site_type = Column(String, nullable=False)  # "single" or "ha"
    unlocode = Column(String(3), nullable=True)  # 3-char UNLOCODE (e.g., "UZC")
    unlocode_resolved = Column(Boolean, default=False)
    cp_search_key = Column(String, nullable=True)  # "US" + unlocode (e.g., "USUZC")
    status = Column(String, default="not_checked")  # not_checked, ready, issues, partial
    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())

    devices = relationship("Device", back_populates="site", cascade="all, delete-orphan")
    verifications = relationship("Verification", back_populates="site", cascade="all, delete-orphan")
