"""
Endpoints de callback — implementados para o Core chamar.

O Core faz HTTP para o serviceUrl registrado em dois momentos:
  1. POST /rides/incoming  — oferta de leilão (responder com proposta ou recusar)
  2. POST /rides/{uuid}/assigned — notificação de vitória no leilão

Estes endpoints são OBRIGATÓRIOS para a integração funcionar.
"""
import os
import asyncio
from datetime import datetime, timezone
from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel
from typing import Optional
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi import Depends

from app.db.database import get_db
from app.db.orm_models import DriverORM, RideORM
from app.models.driver import DriverStatus
from app.models.ride import RideStatus
from app.core import lamport, core_client
from app.core.logging import log_ride_event, logger
from app.core.queue import enqueue_inbox, queue_sizes
from app.core import metrics
from sqlalchemy import select, func
import redis.asyncio as aioredis

router = APIRouter(tags=["core-callbacks"])

GROUP_ID     = os.getenv("SERVICE_NAME", "ridefleet")
MIN_ETA      = int(os.getenv("MIN_ETA_SECONDS", "120"))     # ETA mínimo em segundos
BASE_PRICE   = float(os.getenv("BASE_PRICE", "12.0"))       # Preço base da proposta
REDIS_URL    = os.getenv("REDIS_URL", "redis://redis:6379")
INFLIGHT_KEY = "ridefleet:inflight"   # corridas aceitas e ainda nao concluidas (contador atomico)
# Quantas corridas extras (alem do nº de motoristas) aceitamos como folga.
CAPACITY_SLACK = int(os.getenv("CAPACITY_SLACK", "0"))


async def _driver_total(db: AsyncSession) -> int:
    try:
        return (await db.execute(select(func.count()).select_from(DriverORM))).scalar_one()
    except Exception:
        return 0


async def _inflight_get() -> int:
    try:
        r = aioredis.from_url(REDIS_URL, decode_responses=True)
        val = int(await r.get(INFLIGHT_KEY) or 0)
        await r.aclose()
        return max(0, val)
    except Exception:
        return 0


# ── Schemas dos payloads enviados pelo Core ────────────────────────────────

class Location(BaseModel):
    lat: float
    lng: float
    street: str
    number: str
    city: str
    state: str


class IncomingRidePayload(BaseModel):
    rideUuid: str
    origin: Location
    destination: dict
    originServiceId: str
    passengerId: str
    logicalTimestamp: int
    auctionDeadline: str


class AssignedRidePayload(BaseModel):
    rideUuid: str
    origin: dict
    destination: dict
    passengerId: str
    originServiceId: str
    logicalTimestamp: int
    lockExpiresAt: str


# ── POST /rides/incoming ───────────────────────────────────────────────────

@router.post("/rides/incoming")
async def receive_auction_offer(
    payload: IncomingRidePayload,
    db: AsyncSession = Depends(get_db),
):
    """
    Recebe oferta de leilão do Core.

    Decisão:
      - Se temos motoristas disponíveis → aceita com proposta (200 + ETA/preço)
      - Se não temos motoristas → recusa (204)

    O Core seleciona o vencedor pelo menor preço, desempatando pelo menor ETA.
    """
    # Lamport com timeout: um Redis lento NAO pode segurar a resposta do leilao.
    try:
        new_ts = await asyncio.wait_for(lamport.update(payload.logicalTimestamp), timeout=2.0)
    except Exception:
        new_ts = payload.logicalTimestamp + 1

    log_ride_event(
        "leilao_recebido",
        corrida_id=payload.rideUuid,
        servico_origem=payload.originServiceId,
        estado_novo="evaluating",
    )

    # Conta motoristas disponiveis, tambem com timeout para nao travar o leilao.
    available = None
    try:
        result = await asyncio.wait_for(
            db.execute(
                select(func.count()).where(DriverORM.status == DriverStatus.AVAILABLE)
            ),
            timeout=2.0,
        )
        available = result.scalar_one()
    except Exception as e:
        logger.warning(
            f"contagem de motoristas falhou no leilao: {e}",
            extra={"evento": "leilao_db_timeout"},
        )

    # Recusa apenas quando confirmamos zero motoristas. Se a contagem falhou
    # (available is None), ainda damos lance para nao perder o leilao por uma
    # lentidao pontual de banco/redis.
    # ── Limite por CAPACIDADE ──────────────────────────────────────────────
    # So aceita se houver motorista livre E se ainda nao estamos segurando
    # tantas corridas quanto o total de motoristas. Assim nao vencemos mais
    # leiloes do que conseguimos atender — era o excedente que o Core acabava
    # cancelando por estouro de prazo. Quando um motorista conclui e libera, a
    # folga reaparece e voltamos a aceitar novas corridas.
    if available is not None:
        # Teto RIGIDO por capacidade: nao dar lance se ja estamos segurando
        # tantas corridas quanto motoristas (+ folga). Usa o contador atomico
        # 'inflight' (atualizado na vitoria), que reflete a realidade na hora —
        # diferente da fila pendente, que atrasa e deixava sobre-aceitar.
        total = await _driver_total(db)
        inflight = await _inflight_get()
        teto = (total + CAPACITY_SLACK) if total > 0 else 0
        if available == 0 or (teto > 0 and inflight >= teto):
            log_ride_event(
                "leilao_recusado_capacidade",
                corrida_id=payload.rideUuid,
                nivel="INFO",
                motoristas_livres=available,
                em_andamento=inflight,
                teto=teto,
            )
            return Response(status_code=204)

    n = available if available is not None else 1
    eta = MIN_ETA + max(0, (3 - n) * 30)  # mais motoristas = ETA menor
    price = round(BASE_PRICE + (eta / 60) * 2.5, 2)

    log_ride_event(
        "leilao_proposta_enviada",
        corrida_id=payload.rideUuid,
        estado_novo="proposed",
        motoristas_livres=n,
    )

    return {
        "estimatedEta":   eta,
        "estimatedPrice": price,
        "logicalTimestamp": new_ts,
    }


# ── POST /rides/{rideUuid}/assigned ───────────────────────────────────────

@router.post("/rides/{ride_uuid}/assigned")
async def receive_assignment(
    ride_uuid: str,
    payload: AssignedRidePayload,
    db: AsyncSession = Depends(get_db),
):
    """
    Recebe notificação de que nosso grupo venceu o leilão.

    O Core já transferiu o lock para o nosso grupo.
    Aqui devemos:
      1. Atualizar clock de Lamport
      2. Enfileirar a corrida na inbox para processamento
      3. Retornar 200 confirmando o recebimento
    """
    # ── Teto RIGIDO no momento da vitoria (atomico) ────────────────────────
    # Incrementa o contador de corridas em andamento e, se estourar o numero
    # de motoristas (+ folga), desfaz e REJEITA a atribuicao — assim o Core
    # nao nos entrega mais do que conseguimos atender e nada expira/cancela
    # do nosso lado. O worker decrementa quando conclui/descarta.
    total = await _driver_total(db)
    teto = (total + CAPACITY_SLACK) if total > 0 else 0
    try:
        r = aioredis.from_url(REDIS_URL, decode_responses=True)
        inflight = await r.incr(INFLIGHT_KEY)
        if teto > 0 and inflight > teto:
            await r.decr(INFLIGHT_KEY)
            await r.aclose()
            log_ride_event(
                "assignment_recusado_capacidade",
                corrida_id=ride_uuid,
                nivel="WARN",
                em_andamento=inflight - 1,
                teto=teto,
            )
            raise HTTPException(status_code=409, detail="Sem capacidade: todos os motoristas ocupados")
        await r.aclose()
    except HTTPException:
        raise
    except Exception:
        pass  # se o Redis falhar, nao bloqueia (aceita para nao perder a corrida)

    new_ts = await lamport.update(payload.logicalTimestamp)

    log_ride_event(
        "leilao_ganho",
        corrida_id=ride_uuid,
        servico_origem=payload.originServiceId,
        estado_novo="assigned",
    )

    metrics.inc_received_delegation(payload.originServiceId)

    # Enfileira na inbox — o worker vai atribuir motorista e confirmar
    await enqueue_inbox({
        "id":               ride_uuid,
        "passenger_id":     payload.passengerId,
        "origin":           payload.origin,
        "destination":      payload.destination,
        "delegated_from":   payload.originServiceId,
        "lock_expires_at":  payload.lockExpiresAt,
        "logical_timestamp": new_ts,
    })

    return {"received": True, "logicalTimestamp": new_ts}