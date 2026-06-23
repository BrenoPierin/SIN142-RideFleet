"""
Teste de overload de motoristas.

Cenário: 26 motoristas disponíveis (capacidade local) recebendo 28 corridas.
As 26 primeiras são atendidas localmente (consomem os 26 motoristas); as 2
restantes, criadas quando já não há motorista livre, disparam o overflow e são
enfileiradas na OUTBOX para delegação ao Core.

A delegação propriamente dita (envio ao Core) é feita pelo delegation_worker em
background; aqui validamos o ponto de decisão de forma determinística e offline:
`queue.enqueue_outbox` é chamado EXATAMENTE 2 vezes — as 2 "a mais" que vão pro
Core. Não depende de Redis nem do Core no ar.
"""
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db.database import Base
from app.db.orm_models import DriverORM
from app.models.driver import DriverStatus
from app.models.ride import RideCreate, RideStatus
from app.services import ride_service

CAPACIDADE = 26          # motoristas disponíveis
TOTAL_CORRIDAS = 28      # 26 locais + 2 que vão pro Core
ESPERADO_DELEGADAS = TOTAL_CORRIDAS - CAPACIDADE  # 2


@pytest_asyncio.fixture
async def db_session():
    """Banco SQLite em memória, isolado por teste."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


async def _seed_drivers(db: AsyncSession, n: int) -> None:
    for i in range(n):
        db.add(
            DriverORM(
                id=f"driver-{i:03d}",
                name=f"Motorista {i}",
                license_plate=f"ABC-{i:04d}",
                phone="000",
                status=DriverStatus.AVAILABLE,
            )
        )
    await db.flush()


@pytest.mark.asyncio
async def test_overload_26_motoristas_delega_2_corridas(db_session):
    await _seed_drivers(db_session, CAPACIDADE)

    delegadas = AsyncMock()  # substitui enqueue_outbox e conta as chamadas
    locais = 0

    with patch("app.services.ride_service.queue.enqueue_outbox", delegadas):
        for i in range(TOTAL_CORRIDAS):
            data = RideCreate(
                passenger_id=f"pax-{i:03d}",
                origin="Centro",
                destination="Aeroporto",
            )
            ride = await ride_service.create_ride(db_session, data)

            # Se houver motorista livre, atende localmente (consome um motorista).
            livre = await ride_service.count_available_drivers(db_session)
            if livre >= ride_service.MIN_AVAILABLE_DRIVERS:
                driver = (
                    await db_session.execute(
                        DriverORM.__table__.select()
                        .where(DriverORM.status == DriverStatus.AVAILABLE)
                        .limit(1)
                    )
                ).first()
                await ride_service.transition_ride(
                    db_session, ride.id, RideStatus.MATCH, driver_id=driver.id
                )
                locais += 1

    # As 2 "a mais" foram para a OUTBOX (delegação ao Core).
    assert delegadas.await_count == ESPERADO_DELEGADAS, (
        f"esperava {ESPERADO_DELEGADAS} delegadas, "
        f"obteve {delegadas.await_count}"
    )

    # As 26 primeiras foram atendidas localmente.
    assert locais == CAPACIDADE

    # Capacidade esgotada: nenhum motorista livre ao final.
    restantes = await ride_service.count_available_drivers(db_session)
    assert restantes == 0


@pytest.mark.asyncio
async def test_sem_overload_quando_ha_capacidade(db_session):
    """Controle: 26 motoristas e 26 corridas → nada vai para o Core."""
    await _seed_drivers(db_session, CAPACIDADE)
    delegadas = AsyncMock()

    with patch("app.services.ride_service.queue.enqueue_outbox", delegadas):
        for i in range(CAPACIDADE):
            ride = await ride_service.create_ride(
                db_session,
                RideCreate(passenger_id=f"pax-{i}", origin="A", destination="B"),
            )
            driver = (
                await db_session.execute(
                    DriverORM.__table__.select()
                    .where(DriverORM.status == DriverStatus.AVAILABLE)
                    .limit(1)
                )
            ).first()
            await ride_service.transition_ride(
                db_session, ride.id, RideStatus.MATCH, driver_id=driver.id
            )

    assert delegadas.await_count == 0
