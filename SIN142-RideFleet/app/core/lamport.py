"""
Relógio Lógico de Lamport — Requisito 5 do projeto.

Regras:
  - Ao ENVIAR: incrementa clock local antes de anexar ao evento
  - Ao RECEBER: clock = max(local, recebido) + 1
  - Thread-safe via asyncio.Lock (múltiplas instâncias compartilham Redis)

Referência: Lamport, L. (1978). Time, Clocks, and the Ordering of Events
in a Distributed System. CACM 21(7).
"""
import asyncio
import os
from redis.asyncio import Redis

REDIS_URL    = os.getenv("REDIS_URL", "redis://redis:6379")
SERVICE_NAME = os.getenv("SERVICE_NAME", "ridefleet")

# Chave Redis para o clock — compartilhada entre instâncias do mesmo grupo
CLOCK_KEY = f"ridefleet:lamport:{SERVICE_NAME}"


def _redis() -> Redis:
    return Redis.from_url(REDIS_URL, decode_responses=True)


async def tick() -> int:
    """
    Incrementa e retorna o clock local.
    Usado ao ENVIAR um evento para o Core.
    """
    async with _redis() as r:
        return await r.incr(CLOCK_KEY)


async def update(received: int) -> int:
    """
    Atualiza o clock com base num timestamp recebido do Core.
    Aplica: clock = max(local, recebido) + 1
    Retorna o novo valor do clock.
    """
    async with _redis() as r:
        # Lua script para garantir atomicidade do max+1
        script = """
        local current = tonumber(redis.call('GET', KEYS[1])) or 0
        local received = tonumber(ARGV[1])
        local new_val = math.max(current, received) + 1
        redis.call('SET', KEYS[1], new_val)
        return new_val
        """
        result = await r.eval(script, 1, CLOCK_KEY, received)
        return int(result)


async def current() -> int:
    """Retorna o valor atual do clock sem incrementar."""
    async with _redis() as r:
        val = await r.get(CLOCK_KEY)
        return int(val) if val else 0
