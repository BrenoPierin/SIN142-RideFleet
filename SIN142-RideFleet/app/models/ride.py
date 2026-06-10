from enum import Enum
from typing import Optional
from datetime import datetime
from pydantic import BaseModel, Field
import uuid


class RideStatus(str, Enum):
    REQUEST    = "request"
    MATCH      = "match"
    CONFIRM    = "confirm"
    IN_TRANSIT = "in_transit"
    COMPLETE   = "complete"
    CANCELLED  = "cancelled"


VALID_TRANSITIONS: dict[str, list[str]] = {
    RideStatus.REQUEST:    [RideStatus.MATCH,      RideStatus.CANCELLED],
    RideStatus.MATCH:      [RideStatus.CONFIRM,    RideStatus.CANCELLED],
    RideStatus.CONFIRM:    [RideStatus.IN_TRANSIT, RideStatus.CANCELLED],
    RideStatus.IN_TRANSIT: [RideStatus.COMPLETE,   RideStatus.CANCELLED],
    RideStatus.COMPLETE:   [],
    RideStatus.CANCELLED:  [],
}


class RideCreate(BaseModel):
    passenger_id: str
    origin: str
    destination: str


class RideTransition(BaseModel):
    new_status: RideStatus
    driver_id: Optional[str] = None
    note: Optional[str] = None


class Ride(BaseModel):
    id: str
    passenger_id: str
    origin: str
    destination: str
    status: RideStatus
    driver_id: Optional[str] = None
    delegated_to: Optional[str] = None
    delegated_from: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
