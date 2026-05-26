from typing import Optional
from datetime import datetime
from pydantic import BaseModel


class PassengerCreate(BaseModel):
    name: str
    phone: str


class PassengerUpdate(BaseModel):
    name: Optional[str] = None
    phone: Optional[str] = None


class Passenger(BaseModel):
    id: str
    name: str
    phone: str
    created_at: datetime

    model_config = {"from_attributes": True}
