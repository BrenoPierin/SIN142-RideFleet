"""
Endpoint de métricas Prometheus — GET /metrics.

Atualiza os gauges de ponto-no-tempo (estado do serviço, tamanho das filas e
motoristas disponíveis) no momento do scrape e devolve a exposição no
content-type que o Prometheus espera.

O estado do serviço usa o MESMO critério do /health:
  - 2 (fora_do_ar)     banco ou Redis inacessíveis
  - 1 (congestionado)  sem motoristas OU fila acima do threshold
  - 0 (disponivel)     tudo normal
"""
import os

from fastapi import APIRouter, Depends, Response
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import metrics
from app.core.queue import queue_sizes
from app.db.database import get_db
from app.db.orm_models import DriverORM
from app.models.driver import DriverStatus
from prometheus_client import CONTENT_TYPE_LATEST

router = APIRouter(tags=["metrics"])

QUEUE_WARN_THRESHOLD = int(os.getenv("QUEUE_OVERFLOW_THRESHOLD", "10"))


@router.get("/metrics")
async def prometheus_metrics(db: AsyncSession = Depends(get_db)):
    # Banco acessível?
    db_ok = True
    try:
        await db.execute(text("SELECT 1"))
    except Exception:
        db_ok = False

    # Motoristas disponíveis
    try:
        drivers = (
            await db.execute(
                select(func.count()).where(
                    DriverORM.status == DriverStatus.AVAILABLE
                )
            )
        ).scalar_one()
    except Exception:
        drivers = -1

    # Tamanho das filas (Redis)
    try:
        q = await queue_sizes()
    except Exception:
        q = {"inbox": -1, "outbox": -1}

    # Estado do serviço (mesmo critério do /health)
    if not db_ok or q.get("inbox", -1) == -1:
        state = 2  # fora_do_ar
    elif (
        drivers == 0
        or q.get("inbox", 0) >= QUEUE_WARN_THRESHOLD
        or q.get("outbox", 0) >= QUEUE_WARN_THRESHOLD
    ):
        state = 1  # congestionado
    else:
        state = 0  # disponivel

    metrics.set_runtime_gauges(state, q.get("inbox", -1), q.get("outbox", -1), drivers)

    return Response(content=metrics.render(), media_type=CONTENT_TYPE_LATEST)
