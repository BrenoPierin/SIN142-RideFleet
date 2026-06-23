"""
Lógica de negócio das corridas — Semana 2.
Usa PostgreSQL via SQLAlchemy async + logging estruturado + fila Redis.
"""
import uuid
from datetime import datetime
from typing import Optional
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func

from app.db.orm_models import RideORM, DriverORM
from app.models.ride import RideCreate, RideStatus, VALID_TRANSITIONS
from app.models.driver import DriverStatus
from app.core.logging import log_ride_event
from app.core import queue
from app.core import metrics

import os
MIN_AVAILABLE_DRIVERS = int(os.getenv("MIN_AVAILABLE_DRIVERS", "1"))


async def create_ride(db: AsyncSession, data: RideCreate) -> RideORM:
    ride = RideORM(
        id=str(uuid.uuid4()),
        passenger_id=data.passenger_id,
        origin=data.origin,
        destination=data.destination,
        status=RideStatus.REQUEST,
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    db.add(ride)
    await db.flush()

    log_ride_event("corrida_criada", ride.id, estado_novo=ride.status)

    # Verifica overflow e enfileira na saída se necessário
    available = await count_available_drivers(db)
    if available < MIN_AVAILABLE_DRIVERS:
        await queue.enqueue_outbox({
            "id": ride.id,
            "passenger_id": ride.passenger_id,
            "origin": ride.origin,
            "destination": ride.destination,
            "status": ride.status,
        })

    return ride


async def get_ride(db: AsyncSession, ride_id: str) -> RideORM | None:
    result = await db.execute(select(RideORM).where(RideORM.id == ride_id))
    return result.scalar_one_or_none()


async def list_rides(db: AsyncSession) -> list[RideORM]:
    result = await db.execute(select(RideORM))
    return list(result.scalars().all())


async def list_pending_rides(db: AsyncSession) -> list[RideORM]:
    result = await db.execute(
        select(RideORM).where(RideORM.status == RideStatus.REQUEST)
    )
    return list(result.scalars().all())


async def transition_ride(
    db: AsyncSession,
    ride_id: str,
    new_status: RideStatus,
    driver_id: Optional[str] = None,
) -> RideORM:
    ride = await get_ride(db, ride_id)
    if not ride:
        raise ValueError(f"Corrida {ride_id} não encontrada.")

    allowed = VALID_TRANSITIONS.get(ride.status, [])
    if new_status not in allowed:
        raise ValueError(
            f"Transição inválida: {ride.status} → {new_status}. "
            f"Permitidas: {allowed}"
        )

    # Limitação: só é possível ir para MATCH se houver motorista disponível.
    # (Corridas delegadas recebidas são tratadas pelo inbox_worker, não aqui.)
    if new_status == RideStatus.MATCH and not ride.delegated_from:
        if driver_id:
            cand = await db.get(DriverORM, driver_id)
            if cand is None or cand.status != DriverStatus.AVAILABLE:
                raise ValueError("Motorista informado não está disponível.")
        else:
            cand = (await db.execute(
                select(DriverORM)
                .where(DriverORM.status == DriverStatus.AVAILABLE)
                .limit(1)
            )).scalar_one_or_none()
            if cand is None:
                raise ValueError("Sem motorista disponível para atender a corrida.")
            driver_id = cand.id

    estado_anterior = ride.status
    ride.status = new_status
    ride.updated_at = datetime.utcnow()

    if driver_id:
        ride.driver_id = driver_id

    # Motorista fica BUSY no MATCH
    if new_status == RideStatus.MATCH and driver_id:
        driver = await db.get(DriverORM, driver_id)
        if driver:
            driver.status = DriverStatus.BUSY
            driver.current_ride_id = ride_id
        # Corrida atendida localmente (não veio de delegação de outro grupo).
        if not ride.delegated_from:
            metrics.inc_local_ride()

    # Motorista fica disponível ao final
    if new_status in (RideStatus.COMPLETE, RideStatus.CANCELLED):
        if ride.driver_id:
            driver = await db.get(DriverORM, ride.driver_id)
            if driver:
                driver.status = DriverStatus.AVAILABLE
                driver.current_ride_id = None

    await db.flush()

    log_ride_event(
        "transicao_estado",
        corrida_id=ride_id,
        estado_anterior=estado_anterior,
        estado_novo=new_status,
        nivel="WARN" if new_status == RideStatus.CANCELLED else "INFO",
    )

    return ride


async def count_available_drivers(db: AsyncSession) -> int:
    result = await db.execute(
        select(func.count()).where(DriverORM.status == DriverStatus.AVAILABLE)
    )
    return result.scalar_one()


async def should_delegate(db: AsyncSession) -> bool:
    available = await count_available_drivers(db)
    return available < MIN_AVAILABLE_DRIVERS
