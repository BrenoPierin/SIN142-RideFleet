import uuid
from datetime import datetime
from fastapi import APIRouter, HTTPException, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.db.database import get_db
from app.db.orm_models import PassengerORM
from app.models.passenger import Passenger, PassengerCreate, PassengerUpdate

router = APIRouter(prefix="/passengers", tags=["passengers"])


@router.post("/", response_model=Passenger, status_code=201)
async def create_passenger(data: PassengerCreate, db: AsyncSession = Depends(get_db)):
    passenger = PassengerORM(
        id=str(uuid.uuid4()),
        name=data.name,
        phone=data.phone,
        created_at=datetime.utcnow(),
    )
    db.add(passenger)
    await db.flush()
    return passenger


@router.get("/", response_model=list[Passenger])
async def list_passengers(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(PassengerORM))
    return list(result.scalars().all())


@router.get("/{passenger_id}", response_model=Passenger)
async def get_passenger(passenger_id: str, db: AsyncSession = Depends(get_db)):
    passenger = await db.get(PassengerORM, passenger_id)
    if not passenger:
        raise HTTPException(status_code=404, detail="Passageiro não encontrado.")
    return passenger


@router.patch("/{passenger_id}", response_model=Passenger)
async def update_passenger(passenger_id: str, data: PassengerUpdate, db: AsyncSession = Depends(get_db)):
    passenger = await db.get(PassengerORM, passenger_id)
    if not passenger:
        raise HTTPException(status_code=404, detail="Passageiro não encontrado.")
    for field, value in data.model_dump(exclude_none=True).items():
        setattr(passenger, field, value)
    await db.flush()
    return passenger


@router.delete("/{passenger_id}", status_code=204)
async def delete_passenger(passenger_id: str, db: AsyncSession = Depends(get_db)):
    passenger = await db.get(PassengerORM, passenger_id)
    if not passenger:
        raise HTTPException(status_code=404, detail="Passageiro não encontrado.")
    await db.delete(passenger)
