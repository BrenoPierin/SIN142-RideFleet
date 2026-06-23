"""
Cliente HTTP para o Core do RideFleet.
Encapsula todas as chamadas à API do Core (v0.4.1).

Base URL: definido por CORE_URL (ex.: http://core:8080/api/v1)
Auth: header X-API-Key

Endpoints consumidos:
  POST   /groups/register        — registro idempotente do grupo (retorna apiKey)
  POST   /rides                  — criar corrida e iniciar leilão
  GET    /rides/{uuid}/status    — consultar estado da saga
  GET    /rides/{uuid}/proposals — resultado do leilão
  GET    /rides/{uuid}/audit     — log causal completo
  PATCH  /rides/{uuid}/status    — transição de estado
  POST   /locks/{uuid}           — adquirir/renovar lock distribuído
  DELETE /locks/{uuid}           — liberar lock

NOVIDADE (integração automática da API Key):
  - A apiKey devolvida por /groups/register é persistida em disco
    (CORE_STATE_PATH) e recarregada no próximo boot — assim o serviço
    não depende de a chave estar fixada no .env.
  - `connection_status` guarda o estado da conexão para o /core/status.
"""
import os
import json
import threading
from datetime import datetime, timezone
from pathlib import Path

import httpx

from app.core import lamport
from app.core.logging import logger, log_ride_event

CORE_URL     = os.getenv("CORE_URL", "http://core:8080/api/v1")
GROUP_ID     = os.getenv("SERVICE_NAME", "ridefleet")
SERVICE_URL  = os.getenv("SERVICE_URL", "http://ridefleet-lb:8000")
CONTACT_EMAIL = os.getenv("CORE_CONTACT_EMAIL", f"{GROUP_ID}@ridefleet.example.com")
TIMEOUT      = float(os.getenv("CORE_TIMEOUT", "10"))

# Onde a apiKey é persistida entre reinícios. Pode ser sobrescrito por env.
CORE_STATE_PATH = Path(os.getenv("CORE_STATE_PATH", "./core_state.json"))

# Chave em memória — fonte única de verdade para _headers().
# Precedência: variável de ambiente > arquivo persistido > vazia.
API_KEY = os.getenv("CORE_API_KEY", "")

# Estado observável da conexão (lido pelo endpoint /core/status).
_lock = threading.Lock()
connection_status: dict = {
    "connected":     False,
    "groupId":       GROUP_ID,
    "coreUrl":       CORE_URL,
    "serviceUrl":    SERVICE_URL,
    "apiKeyMasked":  None,
    "registeredAt":  None,
    "lastAttemptAt": None,
    "lastError":     None,
}


# ── Persistência da API Key ────────────────────────────────────────────────

def _mask(key: str) -> str | None:
    if not key:
        return None
    return f"{key[:8]}…" if len(key) > 8 else "…"


def _load_persisted_key() -> None:
    """Carrega a apiKey salva em disco caso o ambiente não a forneça."""
    global API_KEY
    if API_KEY:
        # Ambiente tem prioridade; já registra como conectado.
        with _lock:
            connection_status["connected"] = True
            connection_status["apiKeyMasked"] = _mask(API_KEY)
        return
    try:
        if CORE_STATE_PATH.exists():
            data = json.loads(CORE_STATE_PATH.read_text(encoding="utf-8"))
            key = data.get("apiKey", "")
            if key:
                API_KEY = key
                with _lock:
                    connection_status["connected"] = True
                    connection_status["apiKeyMasked"] = _mask(key)
                    connection_status["registeredAt"] = data.get("registeredAt")
                logger.info(
                    "API Key do Core recuperada do disco.",
                    extra={"evento": "core_key_loaded"},
                )
    except Exception as e:  # pragma: no cover - leitura best-effort
        logger.warning(
            f"Não foi possível carregar a API Key persistida: {e}",
            extra={"evento": "core_key_load_failed"},
        )


def set_api_key(key: str) -> None:
    """Atualiza a apiKey em memória e persiste em disco."""
    global API_KEY
    API_KEY = key
    now = datetime.now(timezone.utc).isoformat()
    with _lock:
        connection_status["connected"] = True
        connection_status["apiKeyMasked"] = _mask(key)
        connection_status["registeredAt"] = now
        connection_status["lastError"] = None
    try:
        CORE_STATE_PATH.write_text(
            json.dumps(
                {"apiKey": key, "groupId": GROUP_ID, "registeredAt": now},
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
    except Exception as e:  # pragma: no cover
        logger.warning(
            f"Falha ao persistir API Key: {e}",
            extra={"evento": "core_key_persist_failed"},
        )


def get_api_key() -> str:
    return API_KEY


def is_connected() -> bool:
    return bool(API_KEY)


def _headers() -> dict:
    return {
        "X-API-Key": API_KEY,
        "Content-Type": "application/json",
    }


# Carrega a chave assim que o módulo é importado.
_load_persisted_key()


# ── Registro ──────────────────────────────────────────────────────────────

async def register_group() -> str:
    """
    Registra o grupo no Core e salva a API Key (persistida em disco).
    Idempotente — pode ser chamado a cada reinício ou via webhook /core/register.
    Retorna a apiKey gerada/existente.

    Levanta httpx.HTTPError em caso de falha (o chamador decide se faz retry).
    """
    with _lock:
        connection_status["lastAttemptAt"] = datetime.now(timezone.utc).isoformat()
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            resp = await client.post(
                f"{CORE_URL}/groups/register",
                json={
                    "groupId":      GROUP_ID,
                    "groupName":    f"{GROUP_ID} — SIN 142",
                    "serviceUrl":   SERVICE_URL,
                    "contactEmail": CONTACT_EMAIL,
                },
            )
            resp.raise_for_status()
            data = resp.json()

        # Tolerante a variações de nome de campo no Core.
        key = data.get("apiKey") or data.get("api_key") or data.get("key")
        if not key:
            raise ValueError(f"Resposta de registro sem apiKey: {data}")

        set_api_key(key)
        logger.info(
            f"Grupo registrado no Core. groupId={GROUP_ID}",
            extra={"evento": "group_registered"},
        )
        return key
    except Exception as e:
        with _lock:
            connection_status["lastError"] = str(e)
        raise


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
            },
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
            },
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
