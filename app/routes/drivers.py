from fastapi import APIRouter, HTTPException
from app.models.driver import Driver, DriverCreate, DriverUpdate, DriverStatus
from app.db import database as db
import uuid
from datetime import datetime

router = APIRouter(prefix="/drivers", tags=["drivers"])


@router.post("/", response_model=Driver, status_code=201)
def create_driver(data: DriverCreate):
    driver = Driver(**data.model_dump())
    db.drivers[driver.id] = driver
    return driver


@router.get("/", response_model=list[Driver])
def list_drivers():
    return list(db.drivers.values())


@router.get("/available", response_model=list[Driver])
def list_available_drivers():
    return [d for d in db.drivers.values() if d.status == DriverStatus.AVAILABLE]


@router.get("/{driver_id}", response_model=Driver)
def get_driver(driver_id: str):
    driver = db.drivers.get(driver_id)
    if not driver:
        raise HTTPException(status_code=404, detail="Motorista não encontrado.")
    return driver


@router.patch("/{driver_id}", response_model=Driver)
def update_driver(driver_id: str, data: DriverUpdate):
    driver = db.drivers.get(driver_id)
    if not driver:
        raise HTTPException(status_code=404, detail="Motorista não encontrado.")
    for field, value in data.model_dump(exclude_none=True).items():
        setattr(driver, field, value)
    return driver


@router.delete("/{driver_id}", status_code=204)
def delete_driver(driver_id: str):
    if driver_id not in db.drivers:
        raise HTTPException(status_code=404, detail="Motorista não encontrado.")
    del db.drivers[driver_id]
