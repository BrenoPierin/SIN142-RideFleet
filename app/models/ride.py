from enum import Enum
from typing import Optional
from datetime import datetime
from pydantic import BaseModel, Field
import uuid


class RideStatus(str, Enum):
    """
    Máquina de estados da corrida.
    Transições válidas:
      request -> match -> confirm -> in_transit -> complete
    Qualquer estado pode ir para -> cancelled
    """
    REQUEST   = "request"
    MATCH     = "match"
    CONFIRM   = "confirm"
    IN_TRANSIT = "in_transit"
    COMPLETE  = "complete"
    CANCELLED = "cancelled"


# Transições permitidas: de qual estado posso ir para qual
VALID_TRANSITIONS: dict[RideStatus, list[RideStatus]] = {
    RideStatus.REQUEST:    [RideStatus.MATCH,     RideStatus.CANCELLED],
    RideStatus.MATCH:      [RideStatus.CONFIRM,   RideStatus.CANCELLED],
    RideStatus.CONFIRM:    [RideStatus.IN_TRANSIT, RideStatus.CANCELLED],
    RideStatus.IN_TRANSIT: [RideStatus.COMPLETE,  RideStatus.CANCELLED],
    RideStatus.COMPLETE:   [],
    RideStatus.CANCELLED:  [],
}


class RideBase(BaseModel):
    passenger_id: str
    origin: str
    destination: str


class RideCreate(RideBase):
    pass


class RideTransition(BaseModel):
    new_status: RideStatus
    driver_id: Optional[str] = None
    note: Optional[str] = None


class Ride(RideBase):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    status: RideStatus = RideStatus.REQUEST
    driver_id: Optional[str] = None
    delegated_to: Optional[str] = None   # id do serviço externo se delegada
    delegated_from: Optional[str] = None # id do serviço de origem se recebida

    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    model_config = {"from_attributes": True}

    def can_transition_to(self, new_status: RideStatus) -> bool:
        return new_status in VALID_TRANSITIONS[self.status]

    def transition(self, new_status: RideStatus, driver_id: Optional[str] = None) -> "Ride":
        if not self.can_transition_to(new_status):
            raise ValueError(
                f"Transição inválida: {self.status} -> {new_status}. "
                f"Transições permitidas: {VALID_TRANSITIONS[self.status]}"
            )
        self.status = new_status
        self.updated_at = datetime.utcnow()
        if driver_id:
            self.driver_id = driver_id
        return self
