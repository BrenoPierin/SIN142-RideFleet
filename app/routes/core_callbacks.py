"""
Endpoints de callback — implementados para o Core chamar.

O Core faz HTTP para o serviceUrl registrado em dois momentos:
  1. POST /rides/incoming  — oferta de leilão (responder com proposta ou recusar)
  2. POST /rides/{uuid}/assigned — notificação de vitória no leilão

Estes endpoints são OBRIGATÓRIOS para a integração funcionar.
"""
import os
from datetime import datetime, timezone
from fastapi import APIRouter, HTTPException
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
from app.core.queue import enqueue_inbox
from sqlalchemy import select, func

router = APIRouter(tags=["core-callbacks"])

GROUP_ID     = os.getenv("SERVICE_NAME", "ridefleet")
MIN_ETA      = int(os.getenv("MIN_ETA_SECONDS", "120"))     # ETA mínimo em segundos
BASE_PRICE   = float(os.getenv("BASE_PRICE", "12.0"))       # Preço base da proposta


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
    # Atualiza clock de Lamport com o timestamp recebido
    new_ts = await lamport.update(payload.logicalTimestamp)

    log_ride_event(
        "leilao_recebido",
        corrida_id=payload.rideUuid,
        servico_origem=payload.originServiceId,
        estado_novo="evaluating",
    )

    # Conta motoristas disponíveis
    result = await db.execute(
        select(func.count()).where(DriverORM.status == DriverStatus.AVAILABLE)
    )
    available = result.scalar_one()

    if available == 0:
        log_ride_event(
            "leilao_recusado",
            corrida_id=payload.rideUuid,
            nivel="WARN",
        )
        # 204 = recusa — passa o leilão
        from fastapi.responses import Response
        return Response(status_code=204)

    # Calcula proposta: ETA baseado em disponibilidade, preço fixo + variação
    eta = MIN_ETA + max(0, (3 - available) * 30)  # mais motoristas = ETA menor
    price = round(BASE_PRICE + (eta / 60) * 2.5, 2)

    log_ride_event(
        "leilao_proposta_enviada",
        corrida_id=payload.rideUuid,
        estado_novo="proposed",
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
    new_ts = await lamport.update(payload.logicalTimestamp)

    log_ride_event(
        "leilao_ganho",
        corrida_id=ride_uuid,
        servico_origem=payload.originServiceId,
        estado_novo="assigned",
    )

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
