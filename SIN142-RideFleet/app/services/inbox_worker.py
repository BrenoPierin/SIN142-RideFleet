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
import httpx
import redis.asyncio as aioredis
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy import select
from app.db.orm_models import DriverORM, RideORM
from app.db.database import Base
from app.models.driver import DriverStatus
from app.models.ride import RideStatus
from app.core import core_client, lamport
from app.core.queue import dequeue_inbox, ack_inbox, reclaim_inbox, read_pending_inbox
from app.core.logging import log_ride_event, logger
from datetime import datetime
import uuid

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql+asyncpg://ridefleet:ridefleet123@db:5432/ridefleet")
SERVICE_NAME = os.getenv("SERVICE_NAME", "ridefleet")
# Identidade UNICA da instancia — cada replica precisa de um nome de
# consumidor proprio no grupo do Redis, senao as duas instancias pegam a
# MESMA corrida e brigam pelo lock/transicao no Core (saga falha -> descarte).
INSTANCE_ID = os.getenv("INSTANCE_ID") or os.getenv("HOSTNAME") or SERVICE_NAME
# Tempo máximo que uma corrida pode esperar na inbox sem motorista antes de
# ser descartada (fallback quando não há lockExpiresAt no payload).
MAX_INBOX_WAIT_S = int(os.getenv("MAX_INBOX_WAIT_SECONDS", "120"))
REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379")
INFLIGHT_KEY = "ridefleet:inflight"   # corridas aceitas e ainda nao concluidas
# Espera entre cada passo da saga (confirm/in_transit/complete). Antes era
# 1s fixo (~3s por corrida). Baixar acelera muito o processamento e ajuda a
# drenar a fila antes dos locks expirarem. 0 = o mais rapido possivel.
SAGA_STEP_DELAY = float(os.getenv("SAGA_STEP_DELAY", "0.1"))


def _parse_iso(raw: str | None):
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except Exception:
        return None


def _nao_da_mais_para_pegar(ride: dict) -> bool:
    """
    True quando a corrida não pode mais ser atendida e deve ser LIMPA da fila:
      - o lock concedido pelo Core (lockExpiresAt) já expirou, ou
      - a corrida está esperando há mais que MAX_INBOX_WAIT_S sem motorista.
    """
    lock = _parse_iso(ride.get("lock_expires_at"))
    if lock is not None:
        agora = datetime.now(lock.tzinfo) if lock.tzinfo else datetime.utcnow()
        if agora > lock:
            return True
    enfileirada = _parse_iso(ride.get("queued_at"))
    if enfileirada is not None:
        base = enfileirada.replace(tzinfo=None)
        if (datetime.utcnow() - base).total_seconds() > MAX_INBOX_WAIT_S:
            return True
    return False


async def _release_slot() -> None:
    """Decrementa o contador de corridas em andamento (libera 1 vaga de
    capacidade). Chamado sempre que uma corrida sai da fila (concluida ou
    descartada), para o /rides/incoming voltar a aceitar."""
    try:
        r = aioredis.from_url(REDIS_URL, decode_responses=True)
        val = await r.decr(INFLIGHT_KEY)
        if val < 0:
            await r.set(INFLIGHT_KEY, 0)
        await r.aclose()
    except Exception:
        pass


async def _ack(msg_id) -> None:
    """ACK na inbox + libera a vaga de capacidade (uma coisa nao pode existir
    sem a outra: toda corrida que sai da fila devolve sua vaga)."""
    await ack_inbox(msg_id)
    await _release_slot()


async def _get_session_factory():
    engine = create_async_engine(DATABASE_URL)
    return async_sessionmaker(engine, expire_on_commit=False)


_SAGA_TO_STATUS = {
    "confirm":    RideStatus.CONFIRM,
    "in_transit": RideStatus.IN_TRANSIT,
    "complete":   RideStatus.COMPLETE,
}


async def _assign_driver(db: AsyncSession, ride_uuid: str) -> DriverORM | None:
    """Atribui o primeiro motorista disponível à corrida (idempotente)."""
    # Se esta corrida já tem motorista atribuído (reprocessamento da fila),
    # reaproveita em vez de consumir outro motorista.
    existing = (await db.execute(
        select(DriverORM).where(DriverORM.current_ride_id == ride_uuid)
    )).scalar_one_or_none()
    if existing:
        return existing

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


async def _free_driver(db: AsyncSession, ride_uuid: str) -> None:
    """Libera o motorista atribuído à corrida — volta a ficar disponível."""
    driver = (await db.execute(
        select(DriverORM).where(DriverORM.current_ride_id == ride_uuid)
    )).scalar_one_or_none()
    if driver:
        driver.status = DriverStatus.AVAILABLE
        driver.current_ride_id = None
        await db.commit()


def _fmt_local(v) -> str:
    """Formata uma localização (dict do Core ou texto) de forma legível,
    ex.: 'Rua J, 123 - Rio Paranaiba'. Evita salvar o dict cru no histórico."""
    if isinstance(v, dict):
        rua    = v.get("street") or v.get("rua") or ""
        num    = v.get("number") or v.get("numero") or ""
        cidade = v.get("city")   or v.get("cidade") or ""
        base = f"{rua}, {num}".strip(", ") if (rua or num) else ""
        partes = [p for p in (base, cidade) if p]
        return " - ".join(partes) if partes else str(v)
    return str(v) if v is not None else ""


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
            origin=_fmt_local(ride_data.get("origin", "")),
            destination=_fmt_local(ride_data.get("destination", "")),
            status=RideStatus.MATCH,
            delegated_from=ride_data.get("delegated_from"),
            created_at=agora,
            updated_at=agora,
        )
        db.add(ride)
    else:
        ride.passenger_id = ride_data.get("passenger_id", ride.passenger_id)
        ride.origin = _fmt_local(ride_data.get("origin", ride.origin))
        ride.destination = _fmt_local(ride_data.get("destination", ride.destination))
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
    consumer = f"{INSTANCE_ID}-inbox-worker"
    session_factory = await _get_session_factory()

    logger.info("Inbox worker iniciado.", extra={"evento": "worker_start"})

    while True:
        try:
            # 1) Relê NOSSAS pendentes — retenta na hora (ex.: surgiu motorista)
            pending = await read_pending_inbox(consumer=consumer)
            # 2) Reivindica pendentes de instâncias que caíram (>60s ociosas)
            claimed = await reclaim_inbox(consumer=consumer)
            vistos = {m["msg_id"] for m in pending}
            claimed = [m for m in claimed if m["msg_id"] not in vistos]
            vistos |= {m["msg_id"] for m in claimed}

            # 3) SEMPRE tenta ler corridas NOVAS. As pendentes NAO podem
            #    bloquear a leitura de novas delegacoes — senao uma unica
            #    corrida presa em retry monopoliza o worker e o grupo para
            #    de receber (starvation). Bloqueio curto se ja ha pendentes,
            #    maior se nao ha nada para fazer.
            block_ms = 1000 if (pending or claimed) else 5000
            novas = await dequeue_inbox(consumer=consumer, count=5, block_ms=block_ms)
            novas = [m for m in novas if m["msg_id"] not in vistos]
            messages = pending + claimed + novas
            leu_nova = bool(novas)

            resolvidas = 0  # concluídas OU descartadas (saíram da fila)

            for msg in messages:
                msg_id  = msg["msg_id"]
                ride    = msg["data"]
                ride_uuid = ride.get("id", "?")

                try:
                    async with session_factory() as db:
                        # 1. Salva corrida no banco local
                        ride_orm = await _save_delegated_ride(db, ride)
                        # 2. Tenta atribuir motorista
                        driver = await _assign_driver(db, ride_uuid)
                        # 3. Registra na corrida QUEM a atendeu (senão o
                        #    histórico mostra "Motorista: —").
                        if driver:
                            ride_orm.driver_id = driver.id
                            await db.commit()

                    if not driver:
                        if _nao_da_mais_para_pegar(ride):
                            # LIMPA a fila: não dá mais para pegar (lock expirado
                            # ou tempo máximo). ACK + cancela a corrida local.
                            async with session_factory() as db:
                                r = await db.get(RideORM, ride_uuid)
                                if r and r.status not in (RideStatus.COMPLETE, RideStatus.CANCELLED):
                                    r.status = RideStatus.CANCELLED
                                    r.updated_at = datetime.utcnow()
                                    await db.commit()
                            await _ack(msg_id)
                            resolvidas += 1
                            log_ride_event(
                                "corrida_delegada_descartada",
                                corrida_id=ride_uuid,
                                nivel="WARN",
                                estado_novo="cancelled",
                                motivo="lock_expirado_ou_timeout",
                            )
                        else:
                            logger.warning(
                                f"Sem motorista para corrida delegada {ride_uuid} — aguardando",
                                extra={"evento": "no_driver", "corrida_id": ride_uuid},
                            )
                            # Não faz ACK — fica pendente e é retentada no próximo
                            # ciclo (assim que um motorista novo ficar disponível).
                        continue

                    log_ride_event(
                        "corrida_delegada_recebida",
                        corrida_id=ride_uuid,
                        servico_origem=ride.get("delegated_from", "externo"),
                        estado_novo="match",
                    )

                    # 3. Adquire lock no Core. Um 409 aqui significa "lock ja
                    #    detido" — como somos o vencedor designado (o Core ja nos
                    #    transferiu o lock na atribuicao), seguimos sem falhar em
                    #    vez de retentar em loop e prender a corrida.
                    try:
                        await core_client.acquire_lock(ride_uuid, ttl=60)
                    except httpx.HTTPStatusError as he:
                        code = he.response.status_code if he.response is not None else None
                        if code == 409:
                            log_ride_event("lock_ja_detido", corrida_id=ride_uuid, nivel="WARN")
                        else:
                            raise

                    # 3b. Idempotencia: se o Core JA concluiu/cancelou esta
                    #     corrida (ex.: um processamento anterior avancou a saga
                    #     mas nao chegou a dar ACK por um restart), NAO reenvia
                    #     transicoes — apenas sincroniza o estado local e
                    #     finaliza. Sem isto, reenviar 'confirm' numa corrida
                    #     'complete' gera 422 em loop e prende a fila.
                    estado_core = None
                    try:
                        st = await core_client.get_ride_status(ride_uuid)
                        if isinstance(st, dict):
                            estado_core = st.get("state") or st.get("status")
                    except Exception:
                        estado_core = None

                    if estado_core in ("complete", "cancelled"):
                        novo = (RideStatus.COMPLETE if estado_core == "complete"
                                else RideStatus.CANCELLED)
                        async with session_factory() as db:
                            r = await db.get(RideORM, ride_uuid)
                            if r:
                                r.status = novo
                                r.updated_at = datetime.utcnow()
                                await db.commit()
                            await _free_driver(db, ride_uuid)
                        await core_client.release_lock(ride_uuid)
                        await _ack(msg_id)
                        resolvidas += 1
                        log_ride_event(
                            "corrida_delegada_sincronizada",
                            corrida_id=ride_uuid,
                            estado_novo=estado_core,
                        )
                        continue

                    # 4. Progride saga no Core e reflete o status no banco local.
                    #    Tolera 422 por passo (Core ja nesse estado / terminal):
                    #    reenviar nao adianta, entao apenas segue e finaliza.
                    for state in ("confirm", "in_transit", "complete"):
                        await asyncio.sleep(SAGA_STEP_DELAY)  # ritmo da saga (configuravel)
                        try:
                            await core_client.transition_ride_core(ride_uuid, state)
                        except httpx.HTTPStatusError as he:
                            code = he.response.status_code if he.response is not None else None
                            if code == 422:
                                log_ride_event(
                                    "transicao_saga_ignorada",
                                    corrida_id=ride_uuid,
                                    estado_novo=state,
                                    nivel="WARN",
                                )
                            else:
                                raise
                        async with session_factory() as db:
                            r = await db.get(RideORM, ride_uuid)
                            if r:
                                r.status = _SAGA_TO_STATUS[state]
                                r.updated_at = datetime.utcnow()
                                await db.commit()
                        log_ride_event(
                            "transicao_saga",
                            corrida_id=ride_uuid,
                            estado_novo=state,
                        )

                    # 5. Libera lock, LIBERA O MOTORISTA e confirma processamento
                    await core_client.release_lock(ride_uuid)
                    async with session_factory() as db:
                        await _free_driver(db, ride_uuid)
                    await _ack(msg_id)
                    resolvidas += 1

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
                    # Rede de seguranca: se a saga falhou e a corrida nao pode
                    # mais ser concluida (lock expirado / tempo maximo), DESCARTA
                    # em vez de retentar para sempre — libera o motorista e
                    # cancela a corrida local, para a fila nao ficar presa.
                    if _nao_da_mais_para_pegar(ride):
                        try:
                            async with session_factory() as db:
                                r = await db.get(RideORM, ride_uuid)
                                if r and r.status not in (RideStatus.COMPLETE, RideStatus.CANCELLED):
                                    r.status = RideStatus.CANCELLED
                                    r.updated_at = datetime.utcnow()
                                    await db.commit()
                                await _free_driver(db, ride_uuid)
                            await _ack(msg_id)
                            resolvidas += 1
                            log_ride_event(
                                "corrida_delegada_descartada",
                                corrida_id=ride_uuid,
                                nivel="WARN",
                                estado_novo="cancelled",
                                motivo="saga_falhou_persistente",
                            )
                        except Exception as e2:
                            logger.error(
                                f"Falha ao descartar corrida presa {ride_uuid}: {e2}",
                                extra={"evento": "discard_error", "corrida_id": ride_uuid},
                            )

            # Pacing: se só havia pendentes para retentar (sem corrida nova e
            # nada resolvido), espera um pouco para não girar o loop a seco.
            if not leu_nova and resolvidas == 0:
                await asyncio.sleep(3)

        except asyncio.CancelledError:
            break
        except Exception as e:
            # Ignora timeouts do Redis — comportamento normal sem mensagens
            if "Timeout" in str(e) or "timeout" in str(e):
                continue   # ← apenas continua o loop, não loga como erro
            logger.error(f"Worker error: {e}", extra={"evento": "worker_error"})
            await asyncio.sleep(2)