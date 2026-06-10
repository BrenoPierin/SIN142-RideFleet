"""
Health check expandido — Semana 2.
Retorna: status geral, motoristas disponíveis, tamanho das filas e latência média.
"""
import time
import os
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, text

from app.db.database import get_db
from app.db.orm_models import DriverORM, RideORM
from app.models.driver import DriverStatus
from app.models.ride import RideStatus
from app.core.queue import queue_sizes
from app.core.logging import logger

router = APIRouter(tags=["health"])

QUEUE_WARN_THRESHOLD = int(os.getenv("QUEUE_OVERFLOW_THRESHOLD", "10"))


@router.get("/health")
async def health_check(db: AsyncSession = Depends(get_db)):
    """
    Retorna o estado completo do serviço:
      - UP: tudo funcionando
      - DEGRADED: fila alta ou poucos motoristas
      - DOWN: banco ou Redis inacessíveis
    """
    start = time.monotonic()
    issues = []

    # Verifica banco
    try:
        await db.execute(text("SELECT 1"))
        db_ok = True
    except Exception as e:
        db_ok = False
        issues.append(f"banco inacessível: {e}")
        logger.error("health_check: banco inacessível", extra={"evento": "health_degraded"})

    # Conta motoristas disponíveis
    try:
        result = await db.execute(
            select(func.count()).where(DriverORM.status == DriverStatus.AVAILABLE)
        )
        available_drivers = result.scalar_one()
    except Exception:
        available_drivers = -1
        issues.append("não foi possível contar motoristas")

    # Tamanho das filas
    try:
        queues = await queue_sizes()
    except Exception as e:
        queues = {"inbox": -1, "outbox": -1}
        issues.append(f"redis inacessível: {e}")
        logger.error("health_check: redis inacessível", extra={"evento": "health_degraded"})

    # Latência desta request
    latency_ms = round((time.monotonic() - start) * 1000, 2)

    # Determina status geral
    if not db_ok or queues["inbox"] == -1:
        status = "DOWN"
    elif (
        available_drivers == 0
        or queues["inbox"] >= QUEUE_WARN_THRESHOLD
        or queues["outbox"] >= QUEUE_WARN_THRESHOLD
    ):
        status = "DEGRADED"
        if available_drivers == 0:
            issues.append("nenhum motorista disponível")
        if queues["inbox"] >= QUEUE_WARN_THRESHOLD:
            issues.append(f"fila de entrada acima do threshold ({queues['inbox']})")
        if queues["outbox"] >= QUEUE_WARN_THRESHOLD:
            issues.append(f"fila de saída acima do threshold ({queues['outbox']})")
    else:
        status = "UP"

    return {
        "status": status,
        "available_drivers": available_drivers,
        "queue": queues,
        "latency_ms": latency_ms,
        "issues": issues,
    }
