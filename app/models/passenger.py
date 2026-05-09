from typing import Optional
from datetime import datetime
from pydantic import BaseModel, Field
import uuid


class PassengerBase(BaseModel):
    name: str
    phone: str


class PassengerCreate(PassengerBase):
    pass


class PassengerUpdate(BaseModel):
    name: Optional[str] = None
    phone: Optional[str] = None


class Passenger(PassengerBase):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    created_at: datetime = Field(default_factory=datetime.utcnow)

    model_config = {"from_attributes": True}
