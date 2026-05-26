from enum import Enum
from typing import Optional
from datetime import datetime
from pydantic import BaseModel


class DriverStatus(str, Enum):
    AVAILABLE = "available"
    BUSY      = "busy"
    OFFLINE   = "offline"


class DriverCreate(BaseModel):
    name: str
    license_plate: str
    phone: str


class DriverUpdate(BaseModel):
    name: Optional[str] = None
    license_plate: Optional[str] = None
    phone: Optional[str] = None
    status: Optional[DriverStatus] = None


class Driver(BaseModel):
    id: str
    name: str
    license_plate: str
    phone: str
    status: DriverStatus
    current_ride_id: Optional[str] = None
    created_at: datetime

    model_config = {"from_attributes": True}
