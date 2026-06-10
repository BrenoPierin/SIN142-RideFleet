"""
Fila de corridas com Redis Streams.

Redis Streams oferecem:
  - Persistência automática das mensagens
  - Grupos de consumidores (consumer groups) — múltiplas instâncias
    processam a fila sem duplicar mensagens
  - ACK explícito — mensagem só sai da fila após processamento confirmado
  - Reprocessamento automático de mensagens travadas (XAUTOCLAIM)

Streams usados:
  INBOX  → ridefleet:stream:inbox   (corridas delegadas recebidas)
  OUTBOX → ridefleet:stream:outbox  (corridas em overflow aguardando delegação)
"""
import json
import os
from datetime import datetime
from redis.asyncio import Redis
from app.core.logging import log_ride_event

REDIS_URL    = os.getenv("REDIS_URL", "redis://redis:6379")
SERVICE_NAME = os.getenv("SERVICE_NAME", "ridefleet")

STREAM_INBOX  = "ridefleet:stream:inbox"
STREAM_OUTBOX = "ridefleet:stream:outbox"
GROUP_NAME    = "ridefleet-consumers"   # mesmo grupo para todas as instâncias

# Mensagens travadas por mais de 60s são reivindicadas por outro consumidor
CLAIM_MIN_IDLE_MS = 60_000
# Tamanho máximo dos streams (evita crescimento infinito)
MAXLEN = 10_000


def get_redis() -> Redis:
    return Redis.from_url(REDIS_URL, decode_responses=True)


async def ensure_groups(r: Redis) -> None:
    """
    Garante que os consumer groups existem nos dois streams.
    MKSTREAM cria o stream se ainda não existir.
    """
    for stream in (STREAM_INBOX, STREAM_OUTBOX):
        try:
            await r.xgroup_create(stream, GROUP_NAME, id="0", mkstream=True)
        except Exception as e:
            if "BUSYGROUP" not in str(e):
                raise


# ── INBOX ──────────────────────────────────────────────────────────────────

async def enqueue_inbox(ride_data: dict) -> str:
    """
    Publica corrida delegada recebida no stream de entrada.
    Retorna o message-id gerado pelo Redis.
    """
    ride_data["queued_at"] = datetime.utcnow().isoformat()
    async with get_redis() as r:
        await ensure_groups(r)
        msg_id = await r.xadd(
            STREAM_INBOX,
            {"payload": json.dumps(ride_data)},
            maxlen=MAXLEN,
            approximate=True,
        )
    log_ride_event(
        "corrida_enfileirada_entrada",
        corrida_id=ride_data.get("id", "?"),
        estado_novo="queued_inbox",
        servico_origem=ride_data.get("delegated_from", "externo"),
    )
    return msg_id


async def dequeue_inbox(consumer: str, count: int = 1, block_ms: int = 5000) -> list[dict]:
    """
    Lê até `count` mensagens do stream de entrada para este consumidor.
    Bloqueia até `block_ms` ms esperando novas mensagens.
    Retorna lista de dicts com {msg_id, data}.
    """
    async with get_redis() as r:
        await ensure_groups(r)
        results = await r.xreadgroup(
            GROUP_NAME, consumer,
            {STREAM_INBOX: ">"},
            count=count,
            block=block_ms,
        )
        if not results:
            return []
        _, messages = results[0]
        return [
            {"msg_id": msg_id, "data": json.loads(fields["payload"])}
            for msg_id, fields in messages
        ]


async def ack_inbox(msg_id: str) -> None:
    """Confirma processamento da mensagem — remove da fila pendente."""
    async with get_redis() as r:
        await r.xack(STREAM_INBOX, GROUP_NAME, msg_id)


async def reclaim_inbox(consumer: str, count: int = 10) -> list[dict]:
    """
    Reivindica mensagens travadas (sem ACK por mais de CLAIM_MIN_IDLE_MS).
    Usado para recuperação após falha de uma instância.
    """
    async with get_redis() as r:
        claimed, _, messages = await r.xautoclaim(
            STREAM_INBOX, GROUP_NAME, consumer,
            min_idle_time=CLAIM_MIN_IDLE_MS,
            start_id="0-0",
            count=count,
        )
        return [
            {"msg_id": msg_id, "data": json.loads(fields["payload"])}
            for msg_id, fields in messages
        ]


# ── OUTBOX ─────────────────────────────────────────────────────────────────

async def enqueue_outbox(ride_data: dict) -> str:
    """
    Publica corrida em overflow no stream de saída para delegação via Core.
    """
    ride_data["queued_at"] = datetime.utcnow().isoformat()
    async with get_redis() as r:
        await ensure_groups(r)
        msg_id = await r.xadd(
            STREAM_OUTBOX,
            {"payload": json.dumps(ride_data)},
            maxlen=MAXLEN,
            approximate=True,
        )
    log_ride_event(
        "corrida_enfileirada_saida",
        corrida_id=ride_data.get("id", "?"),
        estado_novo="queued_outbox",
        nivel="WARN",
    )
    return msg_id


async def dequeue_outbox(consumer: str, count: int = 1, block_ms: int = 5000) -> list[dict]:
    async with get_redis() as r:
        await ensure_groups(r)
        results = await r.xreadgroup(
            GROUP_NAME, consumer,
            {STREAM_OUTBOX: ">"},
            count=count,
            block=block_ms,
        )
        if not results:
            return []
        _, messages = results[0]
        return [
            {"msg_id": msg_id, "data": json.loads(fields["payload"])}
            for msg_id, fields in messages
        ]


async def ack_outbox(msg_id: str) -> None:
    async with get_redis() as r:
        await r.xack(STREAM_OUTBOX, GROUP_NAME, msg_id)


# ── Utilitários ────────────────────────────────────────────────────────────

async def queue_sizes() -> dict:
    """Tamanho atual das filas — usado no /health."""
    async with get_redis() as r:
        try:
            inbox  = await r.xlen(STREAM_INBOX)
            outbox = await r.xlen(STREAM_OUTBOX)
        except Exception:
            inbox = outbox = -1
    return {"inbox": inbox, "outbox": outbox}


async def queue_pending(stream: str) -> int:
    """Número de mensagens lidas mas ainda sem ACK (em processamento)."""
    async with get_redis() as r:
        try:
            info = await r.xpending(stream, GROUP_NAME)
            return info["pending"]
        except Exception:
            return -1
