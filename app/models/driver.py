from enum import Enum
from typing import Optional
from datetime import datetime
from pydantic import BaseModel, Field
import uuid


class DriverStatus(str, Enum):
    AVAILABLE   = "available"
    BUSY        = "busy"
    OFFLINE     = "offline"


class DriverBase(BaseModel):
    name: str
    license_plate: str
    phone: str


class DriverCreate(DriverBase):
    pass


class DriverUpdate(BaseModel):
    name: Optional[str] = None
    license_plate: Optional[str] = None
    phone: Optional[str] = None
    status: Optional[DriverStatus] = None


class Driver(DriverBase):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    status: DriverStatus = DriverStatus.AVAILABLE
    current_ride_id: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)

    model_config = {"from_attributes": True}
