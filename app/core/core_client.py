"""
Cliente HTTP para o Core do RideFleet.
Encapsula todas as chamadas à API do Core (v0.4.1).

Base URL: http://core:8080/api/v1
Auth: header X-API-Key

Endpoints consumidos:
  POST /groups/register        — registro idempotente do grupo
  POST /rides                  — criar corrida e iniciar leilão
  GET  /rides/{uuid}/status    — consultar estado da saga
  GET  /rides/{uuid}/proposals — resultado do leilão
  GET  /rides/{uuid}/audit     — log causal completo
  PATCH /rides/{uuid}/status   — transição de estado
  POST /locks/{uuid}           — adquirir/renovar lock distribuído
  DELETE /locks/{uuid}         — liberar lock
"""
import os
import httpx
from app.core import lamport
from app.core.logging import logger, log_ride_event

CORE_URL     = os.getenv("CORE_URL", "http://core:8080/api/v1")
API_KEY      = os.getenv("CORE_API_KEY", "")
GROUP_ID     = os.getenv("SERVICE_NAME", "ridefleet")
SERVICE_URL  = os.getenv("SERVICE_URL", "http://ridefleet-lb:8000")
TIMEOUT      = float(os.getenv("CORE_TIMEOUT", "10"))


def _headers() -> dict:
    return {
        "X-API-Key": API_KEY,
        "Content-Type": "application/json",
    }


# ── Registro ──────────────────────────────────────────────────────────────

async def register_group() -> str:
    """
    Registra o grupo no Core e salva a API Key.
    Idempotente — pode ser chamado a cada reinício.
    Retorna a apiKey gerada/existente.
    """
    global API_KEY
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        resp = await client.post(
            f"{CORE_URL}/groups/register",
            json={
                "groupId":      GROUP_ID,
                "groupName":    f"{GROUP_ID} — SIN 142",
                "serviceUrl":   SERVICE_URL,
                "contactEmail": f"{GROUP_ID}@ridefleet.example.com",
            }
        )
        resp.raise_for_status()
        data = resp.json()
        API_KEY = data["apiKey"]
        logger.info(
            f"Grupo registrado no Core. groupId={GROUP_ID}",
            extra={"evento": "group_registered"}
        )
        return API_KEY


# ── Corridas ──────────────────────────────────────────────────────────────

async def create_ride_core(
    passenger_id: str,
    origin: dict,
    destination: dict,
    auction_timeout: int = 10,
) -> dict:
    """
    Envia corrida ao Core para iniciar o leilão.
    Retorna o rideUuid gerado pelo Core.
    """
    ts = await lamport.tick()
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        resp = await client.post(
            f"{CORE_URL}/rides",
            headers=_headers(),
            json={
                "originServiceId":       GROUP_ID,
                "passengerId":           passenger_id,
                "origin":                origin,
                "destination":           destination,
                "logicalTimestamp":      ts,
                "auctionTimeoutSeconds": auction_timeout,
            }
        )
        resp.raise_for_status()
        data = resp.json()

        # Atualiza clock com timestamp retornado pelo Core
        if "logicalTimestamp" in data:
            await lamport.update(data["logicalTimestamp"])

        log_ride_event(
            "corrida_enviada_core",
            corrida_id=data.get("rideUuid", "?"),
            estado_novo="request",
        )
        return data


async def get_ride_status(ride_uuid: str) -> dict:
    """Consulta o estado atual da saga + lock no Core."""
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        resp = await client.get(
            f"{CORE_URL}/rides/{ride_uuid}/status",
            headers=_headers(),
        )
        resp.raise_for_status()
        return resp.json()


async def get_ride_proposals(ride_uuid: str) -> dict:
    """Retorna o resultado do leilão (propostas + vencedor)."""
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        resp = await client.get(
            f"{CORE_URL}/rides/{ride_uuid}/proposals",
            headers=_headers(),
        )
        resp.raise_for_status()
        return resp.json()


async def get_ride_audit(ride_uuid: str) -> dict:
    """Retorna o log causal completo da corrida (timestamps Lamport)."""
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        resp = await client.get(
            f"{CORE_URL}/rides/{ride_uuid}/audit",
            headers=_headers(),
        )
        resp.raise_for_status()
        return resp.json()


async def transition_ride_core(
    ride_uuid: str,
    new_state: str,
) -> dict:
    """
    Envia transição de estado ao Core.
    Requer lock ativo no grupo.
    Estados válidos: confirm, in_transit, complete, cancelled
    """
    ts = await lamport.tick()
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        resp = await client.patch(
            f"{CORE_URL}/rides/{ride_uuid}/status",
            headers=_headers(),
            json={
                "newState":         new_state,
                "serviceId":        GROUP_ID,
                "logicalTimestamp": ts,
            }
        )
        resp.raise_for_status()
        data = resp.json()

        if "logicalTimestamp" in data:
            await lamport.update(data["logicalTimestamp"])

        log_ride_event(
            "transicao_enviada_core",
            corrida_id=ride_uuid,
            estado_novo=new_state,
        )
        return data


# ── Locks ─────────────────────────────────────────────────────────────────

async def acquire_lock(ride_uuid: str, ttl: int = 60) -> dict:
    """
    Adquire ou renova lock distribuído no Core.
    Retorna {locked, heldBy, expiresAt}.
    409 → outro grupo detém o lock.
    """
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        resp = await client.post(
            f"{CORE_URL}/locks/{ride_uuid}",
            headers=_headers(),
            json={"serviceId": GROUP_ID, "ttlSeconds": ttl},
        )
        resp.raise_for_status()
        data = resp.json()
        log_ride_event(
            "lock_adquirido",
            corrida_id=ride_uuid,
            nivel="INFO",
        )
        return data


async def release_lock(ride_uuid: str) -> bool:
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        resp = await client.request(
            "DELETE",
            f"{CORE_URL}/locks/{ride_uuid}",
            headers=_headers(),
            json={"serviceId": GROUP_ID},
        )
        success = resp.status_code == 200
        if success:
            log_ride_event("lock_liberado", corrida_id=ride_uuid)
        return success
