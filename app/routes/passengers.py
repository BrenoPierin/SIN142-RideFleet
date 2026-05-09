from fastapi import APIRouter, HTTPException
from app.models.passenger import Passenger, PassengerCreate, PassengerUpdate
from app.db import database as db

router = APIRouter(prefix="/passengers", tags=["passengers"])


@router.post("/", response_model=Passenger, status_code=201)
def create_passenger(data: PassengerCreate):
    passenger = Passenger(**data.model_dump())
    db.passengers[passenger.id] = passenger
    return passenger


@router.get("/", response_model=list[Passenger])
def list_passengers():
    return list(db.passengers.values())


@router.get("/{passenger_id}", response_model=Passenger)
def get_passenger(passenger_id: str):
    passenger = db.passengers.get(passenger_id)
    if not passenger:
        raise HTTPException(status_code=404, detail="Passageiro não encontrado.")
    return passenger


@router.patch("/{passenger_id}", response_model=Passenger)
def update_passenger(passenger_id: str, data: PassengerUpdate):
    passenger = db.passengers.get(passenger_id)
    if not passenger:
        raise HTTPException(status_code=404, detail="Passageiro não encontrado.")
    for field, value in data.model_dump(exclude_none=True).items():
        setattr(passenger, field, value)
    return passenger


@router.delete("/{passenger_id}", status_code=204)
def delete_passenger(passenger_id: str):
    if passenger_id not in db.passengers:
        raise HTTPException(status_code=404, detail="Passageiro não encontrado.")
    del db.passengers[passenger_id]
