"""
Serviço de delegação de saída.

Quando o serviço detecta overflow (sem motoristas disponíveis),
envia a corrida ao Core para iniciar o leilão com outros grupos.

Fluxo:
  corrida criada → sem motorista → enqueue_outbox
  → delegation_worker lê outbox → POST /api/v1/rides no Core
  → Core faz leilão → vencedor recebe /rides/assigned
  → Core notifica via RabbitMQ (ride_status_changed)
"""
import asyncio
import os
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from app.core import core_client, lamport
from app.core.queue import dequeue_outbox, ack_outbox
from app.core.logging import log_ride_event, logger

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql+asyncpg://ridefleet:ridefleet123@db:5432/ridefleet")
SERVICE_NAME = os.getenv("SERVICE_NAME", "ridefleet")


def _make_location(addr: str) -> dict:
    """Converte string de endereço para o schema de Location do Core."""
    return {
        "lat": -20.75, "lng": -42.88,
        "street": addr, "number": "0",
        "city": "Viçosa", "state": "MG",
    }


async def delegation_worker():
    """
    Worker que consome a fila de saída e delega corridas ao Core.
    Roda em background como task assíncrona.
    """
    consumer = f"{SERVICE_NAME}-delegation-worker"
    logger.info("Delegation worker iniciado.", extra={"evento": "worker_start"})

    while True:
        try:
            messages = await dequeue_outbox(consumer=consumer, count=1, block_ms=5000)
            for msg in messages:
                msg_id = msg["msg_id"]
                ride   = msg["data"]
                ride_id = ride.get("id", "?")

                try:
                    # Monta payload para o Core
                    origin      = _make_location(ride.get("origin", ""))
                    destination = _make_location(ride.get("destination", ""))

                    result = await core_client.create_ride_core(
                        passenger_id=ride.get("passenger_id", ""),
                        origin=origin,
                        destination=destination,
                        auction_timeout=10,
                    )

                    log_ride_event(
                        "corrida_delegada_core",
                        corrida_id=ride_id,
                        estado_novo="delegated",
                    )

                    await ack_outbox(msg_id)

                except Exception as e:
                    logger.error(
                        f"Falha ao delegar corrida {ride_id}: {e}",
                        extra={"evento": "delegation_error", "corrida_id": ride_id}
                    )
                    # Não faz ACK — mensagem fica pendente para reprocessamento

        except asyncio.CancelledError:
            break
        except Exception as e:
            # Ignora timeouts do Redis — comportamento normal sem mensagens
            if "Timeout" in str(e) or "timeout" in str(e):
                continue   # ← apenas continua o loop, não loga como erro
            logger.error(f"Worker error: {e}", extra={"evento": "worker_error"})
            await asyncio.sleep(2)
