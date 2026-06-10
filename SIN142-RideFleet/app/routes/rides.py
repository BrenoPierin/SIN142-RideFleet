from fastapi import APIRouter, HTTPException, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.database import get_db
from app.models.ride import Ride, RideCreate, RideTransition
from app.services import ride_service
from app.core.queue import queue_sizes

router = APIRouter(prefix="/rides", tags=["rides"])


@router.post("/", response_model=Ride, status_code=201)
async def create_ride(data: RideCreate, db: AsyncSession = Depends(get_db)):
    return await ride_service.create_ride(db, data)


@router.get("/", response_model=list[Ride])
async def list_rides(db: AsyncSession = Depends(get_db)):
    return await ride_service.list_rides(db)


@router.get("/pending", response_model=list[Ride])
async def list_pending_rides(db: AsyncSession = Depends(get_db)):
    return await ride_service.list_pending_rides(db)


@router.get("/overflow/check")
async def check_overflow(db: AsyncSession = Depends(get_db)):
    delegate = await ride_service.should_delegate(db)
    available = await ride_service.count_available_drivers(db)
    queues = await queue_sizes()
    return {
        "should_delegate": delegate,
        "available_drivers": available,
        "queue": queues,
    }


@router.get("/{ride_id}", response_model=Ride)
async def get_ride(ride_id: str, db: AsyncSession = Depends(get_db)):
    ride = await ride_service.get_ride(db, ride_id)
    if not ride:
        raise HTTPException(status_code=404, detail="Corrida não encontrada.")
    return ride


@router.patch("/{ride_id}/status", response_model=Ride)
async def transition_ride(ride_id: str, body: RideTransition, db: AsyncSession = Depends(get_db)):
    try:
        return await ride_service.transition_ride(db, ride_id, body.new_status, body.driver_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
