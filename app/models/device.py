from sqlalchemy import Column, Integer, String, ForeignKey
from sqlalchemy.orm import relationship

from app.database import Base


class Device(Base):
    __tablename__ = "devices"

    id = Column(Integer, primary_key=True, autoincrement=True)
    site_id = Column(String, ForeignKey("sites.id"), nullable=False)
    port = Column(Integer, nullable=False)  # 1, 2, 3, or 4
    role = Column(String, nullable=False)  # "old_primary", "old_secondary", "new_primary", "new_secondary"
    expected_hostname = Column(String, nullable=False)

    site = relationship("Site", back_populates="devices")
