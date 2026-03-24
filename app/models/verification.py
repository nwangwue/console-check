from sqlalchemy import Column, Integer, String, Text, DateTime, ForeignKey
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship

from app.database import Base


class Verification(Base):
    __tablename__ = "verifications"

    id = Column(Integer, primary_key=True, autoincrement=True)
    site_id = Column(String, ForeignKey("sites.id"), nullable=False)
    port = Column(Integer, nullable=False)
    expected_hostname = Column(String, nullable=False)
    found_hostname = Column(String, nullable=True)
    raw_output = Column(Text, nullable=True)
    verdict = Column(String, nullable=False)  # match, mismatch, swapped, no_response, unknown
    swap_details = Column(String, nullable=True)
    engineer = Column(String, nullable=True)
    timestamp = Column(DateTime, default=func.now())

    site = relationship("Site", back_populates="verifications")
