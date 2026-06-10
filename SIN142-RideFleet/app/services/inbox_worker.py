"""
Worker de processamento da inbox.

Processa corridas delegadas recebidas de outros grupos:
  1. Lê da fila inbox
  2. Atribui motorista disponível localmente
  3. Adquire lock no Core
  4. Progride a saga: confirm → in_transit → complete
  5. Faz ACK na fila
"""
import asyncio
import os
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy import select
from app.db.orm_models import DriverORM, RideORM
from app.db.database import Base
from app.models.driver import DriverStatus
from app.models.ride import RideStatus
from app.core import core_client, lamport
from app.core.queue import dequeue_inbox, ack_inbox, reclaim_inbox
from app.core.logging import log_ride_event, logger
from datetime import datetime
import uuid

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql+asyncpg://ridefleet:ridefleet123@db:5432/ridefleet")
SERVICE_NAME = os.getenv("SERVICE_NAME", "ridefleet")


async def _get_session_factory():
    engine = create_async_engine(DATABASE_URL)
    return async_sessionmaker(engine, expire_on_commit=False)


async def _assign_driver(db: AsyncSession, ride_uuid: str) -> DriverORM | None:
    """Atribui o primeiro motorista disponível à corrida."""
    result = await db.execute(
        select(DriverORM)
        .where(DriverORM.status == DriverStatus.AVAILABLE)
        .limit(1)
    )
    driver = result.scalar_one_or_none()
    if driver:
        driver.status = DriverStatus.BUSY
        driver.current_ride_id = ride_uuid
        await db.commit()
    return driver


async def _save_delegated_ride(db: AsyncSession, ride_data: dict) -> RideORM:
    """Salva (ou atualiza) a corrida delegada recebida no banco local.

    Idempotente: se a corrida ja existe (reprocessamento da fila ou
    corrida ja delegada anteriormente), atualiza em vez de tentar um
    INSERT que violaria a PK rides_pkey.
    """
    agora = datetime.utcnow()
    ride = await db.get(RideORM, ride_data["id"])
    if ride is None:
        ride = RideORM(
            id=ride_data["id"],
            passenger_id=ride_data.get("passenger_id", ""),
            origin=str(ride_data.get("origin", "")),
            destination=str(ride_data.get("destination", "")),
            status=RideStatus.MATCH,
            delegated_from=ride_data.get("delegated_from"),
            created_at=agora,
            updated_at=agora,
        )
        db.add(ride)
    else:
        ride.passenger_id = ride_data.get("passenger_id", ride.passenger_id)
        ride.origin = str(ride_data.get("origin", ride.origin))
        ride.destination = str(ride_data.get("destination", ride.destination))
        ride.status = RideStatus.MATCH
        ride.delegated_from = ride_data.get("delegated_from", ride.delegated_from)
        ride.updated_at = agora
    await db.commit()
    return ride


async def inbox_worker():
    """
    Worker principal da inbox.
    Roda em background e processa corridas delegadas recebidas.
    """
    consumer = f"{SERVICE_NAME}-inbox-worker"
    session_factory = await _get_session_factory()

    logger.info("Inbox worker iniciado.", extra={"evento": "worker_start"})

    while True:
        try:
            # Reivindica mensagens travadas primeiro
            claimed = await reclaim_inbox(consumer=consumer)
            messages = claimed or await dequeue_inbox(consumer=consumer, count=1, block_ms=5000)

            for msg in messages:
                msg_id  = msg["msg_id"]
                ride    = msg["data"]
                ride_uuid = ride.get("id", "?")

                try:
                    async with session_factory() as db:
                        # 1. Salva corrida no banco local
                        await _save_delegated_ride(db, ride)

                        # 2. Tenta atribuir motorista
                        driver = await _assign_driver(db, ride_uuid)

                    if not driver:
                        logger.warning(
                            f"Sem motorista para corrida delegada {ride_uuid}",
                            extra={"evento": "no_driver", "corrida_id": ride_uuid}
                        )
                        # Não faz ACK — será reprocessado
                        continue

                    log_ride_event(
                        "corrida_delegada_recebida",
                        corrida_id=ride_uuid,
                        servico_origem=ride.get("delegated_from", "externo"),
                        estado_novo="match",
                    )

                    # 3. Adquire lock no Core
                    await core_client.acquire_lock(ride_uuid, ttl=60)

                    # 4. Progride saga no Core
                    for state in ("confirm", "in_transit", "complete"):
                        await asyncio.sleep(1)  # simula processamento real
                        await core_client.transition_ride_core(ride_uuid, state)
                        log_ride_event(
                            "transicao_saga",
                            corrida_id=ride_uuid,
                            estado_novo=state,
                        )

                    # 5. Libera lock e confirma processamento
                    await core_client.release_lock(ride_uuid)
                    await ack_inbox(msg_id)

                    log_ride_event(
                        "corrida_delegada_concluida",
                        corrida_id=ride_uuid,
                        estado_novo="complete",
                    )

                except Exception as e:
                    logger.error(
                        f"Erro ao processar corrida delegada {ride_uuid}: {e}",
                        extra={"evento": "inbox_processing_error", "corrida_id": ride_uuid}
                    )

        except asyncio.CancelledError:
            break
        except Exception as e:
            # Ignora timeouts do Redis — comportamento normal sem mensagens
            if "Timeout" in str(e) or "timeout" in str(e):
                continue   # ← apenas continua o loop, não loga como erro
            logger.error(f"Worker error: {e}", extra={"evento": "worker_error"})
            await asyncio.sleep(2)
