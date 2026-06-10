import uuid
from datetime import datetime
from fastapi import APIRouter, HTTPException, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.db.database import get_db
from app.db.orm_models import DriverORM
from app.models.driver import Driver, DriverCreate, DriverUpdate, DriverStatus

router = APIRouter(prefix="/drivers", tags=["drivers"])


@router.post("/", response_model=Driver, status_code=201)
async def create_driver(data: DriverCreate, db: AsyncSession = Depends(get_db)):
    driver = DriverORM(
        id=str(uuid.uuid4()),
        name=data.name,
        license_plate=data.license_plate,
        phone=data.phone,
        status=DriverStatus.AVAILABLE,
        created_at=datetime.utcnow(),
    )
    db.add(driver)
    await db.flush()
    return driver


@router.get("/", response_model=list[Driver])
async def list_drivers(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(DriverORM))
    return list(result.scalars().all())


@router.get("/available", response_model=list[Driver])
async def list_available(db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(DriverORM).where(DriverORM.status == DriverStatus.AVAILABLE)
    )
    return list(result.scalars().all())


@router.get("/{driver_id}", response_model=Driver)
async def get_driver(driver_id: str, db: AsyncSession = Depends(get_db)):
    driver = await db.get(DriverORM, driver_id)
    if not driver:
        raise HTTPException(status_code=404, detail="Motorista não encontrado.")
    return driver


@router.patch("/{driver_id}", response_model=Driver)
async def update_driver(driver_id: str, data: DriverUpdate, db: AsyncSession = Depends(get_db)):
    driver = await db.get(DriverORM, driver_id)
    if not driver:
        raise HTTPException(status_code=404, detail="Motorista não encontrado.")
    for field, value in data.model_dump(exclude_none=True).items():
        setattr(driver, field, value)
    await db.flush()
    return driver


@router.delete("/{driver_id}", status_code=204)
async def delete_driver(driver_id: str, db: AsyncSession = Depends(get_db)):
    driver = await db.get(DriverORM, driver_id)
    if not driver:
        raise HTTPException(status_code=404, detail="Motorista não encontrado.")
    await db.delete(driver)
