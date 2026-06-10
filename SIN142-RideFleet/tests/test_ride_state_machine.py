"""
Testes unitários — Semana 1 (pendência) + Semana 2
Cobertura:
  - Máquina de estados das corridas (VALID_TRANSITIONS)
  - Criação e transição via ride_service (banco em memória com SQLite async)
  - Política de overflow (should_delegate)
  - Logging estruturado (JSONFormatter)
  - Tamanho e formato dos campos do log

SIN 142 — Sistemas Distribuídos — UFV 2026/1
"""

import json
import logging
import pytest
import pytest_asyncio
from unittest.mock import AsyncMock, patch, MagicMock

# ── Configuração do event loop para pytest-asyncio ─────────────────────────
pytest_plugins = ["pytest_asyncio"]


# ==========================================================================
# 1. MÁQUINA DE ESTADOS — testes puros (sem banco)
# ==========================================================================

from app.models.ride import RideStatus, VALID_TRANSITIONS


class TestRideStateMachine:
    """Valida a tabela VALID_TRANSITIONS conforme especificado na Semana 1."""

    # Transições válidas esperadas
    EXPECTED_VALID = [
        (RideStatus.REQUEST,    RideStatus.MATCH),
        (RideStatus.REQUEST,    RideStatus.CANCELLED),
        (RideStatus.MATCH,      RideStatus.CONFIRM),
        (RideStatus.MATCH,      RideStatus.CANCELLED),
        (RideStatus.CONFIRM,    RideStatus.IN_TRANSIT),
        (RideStatus.CONFIRM,    RideStatus.CANCELLED),
        (RideStatus.IN_TRANSIT, RideStatus.COMPLETE),
        (RideStatus.IN_TRANSIT, RideStatus.CANCELLED),
    ]

    # Transições inválidas esperadas (não devem estar na tabela)
    EXPECTED_INVALID = [
        (RideStatus.REQUEST,    RideStatus.IN_TRANSIT),
        (RideStatus.REQUEST,    RideStatus.COMPLETE),
        (RideStatus.MATCH,      RideStatus.REQUEST),
        (RideStatus.COMPLETE,   RideStatus.REQUEST),
        (RideStatus.CANCELLED,  RideStatus.REQUEST),
        (RideStatus.COMPLETE,   RideStatus.CANCELLED),
        (RideStatus.IN_TRANSIT, RideStatus.REQUEST),
    ]

    @pytest.mark.parametrize("origin,dest", EXPECTED_VALID)
    def test_valid_transition_exists(self, origin, dest):
        assert dest in VALID_TRANSITIONS[origin], (
            f"Transição {origin} → {dest} deveria ser válida mas não está em VALID_TRANSITIONS"
        )

    @pytest.mark.parametrize("origin,dest", EXPECTED_INVALID)
    def test_invalid_transition_absent(self, origin, dest):
        assert dest not in VALID_TRANSITIONS.get(origin, []), (
            f"Transição {origin} → {dest} não deveria ser válida"
        )

    def test_terminal_states_have_no_transitions(self):
        assert VALID_TRANSITIONS[RideStatus.COMPLETE] == []
        assert VALID_TRANSITIONS[RideStatus.CANCELLED] == []

    def test_all_statuses_covered(self):
        for status in RideStatus:
            assert status in VALID_TRANSITIONS, (
                f"Status {status} não possui entrada em VALID_TRANSITIONS"
            )

    def test_request_is_initial_state(self):
        """REQUEST deve ser alcançável como estado inicial (não é destino de nenhuma transição)."""
        all_destinations = {
            dest
            for destinations in VALID_TRANSITIONS.values()
            for dest in destinations
        }
        assert RideStatus.REQUEST not in all_destinations, (
            "REQUEST não deve ser destino de nenhuma transição (é estado inicial)"
        )


# ==========================================================================
# 2. MODELOS PYDANTIC — validação de schema
# ==========================================================================

from app.models.ride import RideCreate, RideTransition, Ride
from app.models.driver import Driver, DriverCreate, DriverStatus
from app.models.passenger import Passenger, PassengerCreate
from datetime import datetime


class TestRideModels:
    def test_ride_create_valid(self):
        r = RideCreate(passenger_id="p1", origin="A", destination="B")
        assert r.passenger_id == "p1"
        assert r.origin == "A"
        assert r.destination == "B"

    def test_ride_transition_valid_status(self):
        t = RideTransition(new_status=RideStatus.MATCH, driver_id="d1")
        assert t.new_status == RideStatus.MATCH
        assert t.driver_id == "d1"

    def test_ride_transition_optional_driver(self):
        t = RideTransition(new_status=RideStatus.CANCELLED)
        assert t.driver_id is None

    def test_ride_status_string_enum(self):
        """RideStatus deve ser string para serialização JSON."""
        assert isinstance(RideStatus.REQUEST, str)
        assert RideStatus.REQUEST == "request"

    def test_driver_status_enum(self):
        assert DriverStatus.AVAILABLE == "available"
        assert DriverStatus.BUSY == "busy"
        assert DriverStatus.OFFLINE == "offline"


class TestDriverModels:
    def test_driver_create(self):
        d = DriverCreate(name="João", license_plate="ABC-1234", phone="11999999999")
        assert d.name == "João"

    def test_driver_update_partial(self):
        from app.models.driver import DriverUpdate
        u = DriverUpdate(status=DriverStatus.OFFLINE)
        dumped = u.model_dump(exclude_none=True)
        assert dumped == {"status": DriverStatus.OFFLINE}
        assert "name" not in dumped


class TestPassengerModels:
    def test_passenger_create(self):
        p = PassengerCreate(name="Maria", phone="21988888888")
        assert p.name == "Maria"
        assert p.phone == "21988888888"


# ==========================================================================
# 3. LOGGING ESTRUTURADO — formato JSON e campos obrigatórios
# ==========================================================================

from app.core.logging import JSONFormatter, log_ride_event, get_logger


class TestJSONFormatter:
    def _make_record(self, msg="test", level=logging.INFO, extra=None):
        record = logging.LogRecord(
            name="test", level=level,
            pathname="", lineno=0,
            msg=msg, args=(), exc_info=None,
        )
        if extra:
            for k, v in extra.items():
                setattr(record, k, v)
        return record

    def test_output_is_valid_json(self):
        fmt = JSONFormatter()
        record = self._make_record("hello")
        output = fmt.format(record)
        parsed = json.loads(output)
        assert isinstance(parsed, dict)

    def test_required_fields_present(self):
        fmt = JSONFormatter()
        record = self._make_record(
            "corrida criada",
            extra={
                "evento": "corrida_criada",
                "corrida_id": "abc-123",
                "servico_origem": "ridefleet-1",
                "estado_anterior": None,
                "estado_novo": "request",
            }
        )
        parsed = json.loads(fmt.format(record))
        for field in ("timestamp", "level", "service", "evento", "corrida_id",
                      "servico_origem", "estado_anterior", "estado_novo"):
            assert field in parsed, f"Campo obrigatório '{field}' ausente no log"

    def test_log_level_info(self):
        fmt = JSONFormatter()
        record = self._make_record(level=logging.INFO)
        parsed = json.loads(fmt.format(record))
        assert parsed["level"] == "INFO"

    def test_log_level_warning(self):
        fmt = JSONFormatter()
        record = self._make_record(level=logging.WARNING)
        parsed = json.loads(fmt.format(record))
        assert parsed["level"] == "WARNING"

    def test_log_level_error(self):
        fmt = JSONFormatter()
        record = self._make_record(level=logging.ERROR)
        parsed = json.loads(fmt.format(record))
        assert parsed["level"] == "ERROR"

    def test_timestamp_is_iso8601(self):
        fmt = JSONFormatter()
        record = self._make_record()
        parsed = json.loads(fmt.format(record))
        # Deve parsear sem erro
        ts = datetime.fromisoformat(parsed["timestamp"].replace("Z", "+00:00"))
        assert ts is not None

    def test_optional_fields_omitted_when_absent(self):
        """Campos extras não passados não devem aparecer no log."""
        fmt = JSONFormatter()
        record = self._make_record("simple message")
        parsed = json.loads(fmt.format(record))
        # Campos opcionais só aparecem se passados via extra={}
        # Aqui não foram passados, então não devem estar
        for field in ("evento", "corrida_id"):
            assert field not in parsed


class TestLogRideEvent:
    def test_log_ride_event_info(self, caplog):
        with caplog.at_level(logging.INFO):
            log_ride_event(
                "corrida_criada", "ride-001",
                estado_novo="request"
            )
        assert len(caplog.records) >= 1

    def test_log_ride_event_warn(self, caplog):
        with caplog.at_level(logging.WARNING):
            log_ride_event(
                "corrida_cancelada", "ride-002",
                estado_anterior="match", estado_novo="cancelled",
                nivel="WARN"
            )
        assert any(r.levelno == logging.WARNING for r in caplog.records)

    def test_log_ride_event_error(self, caplog):
        with caplog.at_level(logging.ERROR):
            log_ride_event(
                "falha_banco", "ride-003",
                nivel="ERROR"
            )
        assert any(r.levelno == logging.ERROR for r in caplog.records)


# ==========================================================================
# 4. RIDE SERVICE — lógica de negócio com banco mockado (SQLite async)
# ==========================================================================

from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import DeclarativeBase
from app.db.database import Base
from app.db.orm_models import RideORM, DriverORM, PassengerORM
from app.services import ride_service


@pytest_asyncio.fixture
async def db_session():
    """Banco SQLite em memória para testes isolados."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with session_factory() as session:
        yield session

    await engine.dispose()


@pytest.fixture
def sample_ride_create():
    return RideCreate(passenger_id="passenger-1", origin="Centro", destination="Aeroporto")


@pytest.mark.asyncio
class TestRideService:

    async def test_create_ride_returns_orm(self, db_session, sample_ride_create):
        with patch("app.services.ride_service.queue.enqueue_outbox", new_callable=AsyncMock):
            ride = await ride_service.create_ride(db_session, sample_ride_create)
        assert ride.id is not None
        assert ride.status == RideStatus.REQUEST
        assert ride.passenger_id == "passenger-1"
        assert ride.origin == "Centro"
        assert ride.destination == "Aeroporto"

    async def test_create_ride_status_is_request(self, db_session, sample_ride_create):
        with patch("app.services.ride_service.queue.enqueue_outbox", new_callable=AsyncMock):
            ride = await ride_service.create_ride(db_session, sample_ride_create)
        assert ride.status == RideStatus.REQUEST

    async def test_get_ride_existing(self, db_session, sample_ride_create):
        with patch("app.services.ride_service.queue.enqueue_outbox", new_callable=AsyncMock):
            created = await ride_service.create_ride(db_session, sample_ride_create)
        found = await ride_service.get_ride(db_session, created.id)
        assert found is not None
        assert found.id == created.id

    async def test_get_ride_not_found(self, db_session):
        result = await ride_service.get_ride(db_session, "nonexistent-id")
        assert result is None

    async def test_list_rides_empty(self, db_session):
        rides = await ride_service.list_rides(db_session)
        assert rides == []

    async def test_list_rides_after_create(self, db_session, sample_ride_create):
        with patch("app.services.ride_service.queue.enqueue_outbox", new_callable=AsyncMock):
            await ride_service.create_ride(db_session, sample_ride_create)
        rides = await ride_service.list_rides(db_session)
        assert len(rides) == 1

    async def test_transition_request_to_match(self, db_session, sample_ride_create):
        # Cria motorista disponível para evitar overflow
        driver = DriverORM(id="d1", name="João", license_plate="ABC-1", phone="11999", status="available")
        db_session.add(driver)
        await db_session.flush()

        with patch("app.services.ride_service.queue.enqueue_outbox", new_callable=AsyncMock):
            ride = await ride_service.create_ride(db_session, sample_ride_create)

        updated = await ride_service.transition_ride(db_session, ride.id, RideStatus.MATCH, "d1")
        assert updated.status == RideStatus.MATCH
        assert updated.driver_id == "d1"

    async def test_transition_invalid_raises_value_error(self, db_session, sample_ride_create):
        driver = DriverORM(id="d2", name="Maria", license_plate="XYZ-9", phone="21888", status="available")
        db_session.add(driver)
        await db_session.flush()

        with patch("app.services.ride_service.queue.enqueue_outbox", new_callable=AsyncMock):
            ride = await ride_service.create_ride(db_session, sample_ride_create)

        with pytest.raises(ValueError, match="Transição inválida"):
            await ride_service.transition_ride(db_session, ride.id, RideStatus.COMPLETE)

    async def test_full_ride_lifecycle(self, db_session, sample_ride_create):
        """Percorre request → match → confirm → in_transit → complete."""
        driver = DriverORM(id="d3", name="Pedro", license_plate="DEF-5", phone="31777", status="available")
        db_session.add(driver)
        await db_session.flush()

        with patch("app.services.ride_service.queue.enqueue_outbox", new_callable=AsyncMock):
            ride = await ride_service.create_ride(db_session, sample_ride_create)

        ride = await ride_service.transition_ride(db_session, ride.id, RideStatus.MATCH, "d3")
        assert ride.status == RideStatus.MATCH

        ride = await ride_service.transition_ride(db_session, ride.id, RideStatus.CONFIRM)
        assert ride.status == RideStatus.CONFIRM

        ride = await ride_service.transition_ride(db_session, ride.id, RideStatus.IN_TRANSIT)
        assert ride.status == RideStatus.IN_TRANSIT

        ride = await ride_service.transition_ride(db_session, ride.id, RideStatus.COMPLETE)
        assert ride.status == RideStatus.COMPLETE

    async def test_driver_becomes_busy_on_match(self, db_session, sample_ride_create):
        driver = DriverORM(id="d4", name="Ana", license_plate="GHI-2", phone="71666", status="available")
        db_session.add(driver)
        await db_session.flush()

        with patch("app.services.ride_service.queue.enqueue_outbox", new_callable=AsyncMock):
            ride = await ride_service.create_ride(db_session, sample_ride_create)

        await ride_service.transition_ride(db_session, ride.id, RideStatus.MATCH, "d4")
        await db_session.refresh(driver)
        assert driver.status == DriverStatus.BUSY
        assert driver.current_ride_id == ride.id

    async def test_driver_becomes_available_on_complete(self, db_session, sample_ride_create):
        driver = DriverORM(id="d5", name="Lucas", license_plate="JKL-3", phone="81555", status="available")
        db_session.add(driver)
        await db_session.flush()

        with patch("app.services.ride_service.queue.enqueue_outbox", new_callable=AsyncMock):
            ride = await ride_service.create_ride(db_session, sample_ride_create)

        ride = await ride_service.transition_ride(db_session, ride.id, RideStatus.MATCH, "d5")
        ride = await ride_service.transition_ride(db_session, ride.id, RideStatus.CONFIRM)
        ride = await ride_service.transition_ride(db_session, ride.id, RideStatus.IN_TRANSIT)
        ride = await ride_service.transition_ride(db_session, ride.id, RideStatus.COMPLETE)

        await db_session.refresh(driver)
        assert driver.status == DriverStatus.AVAILABLE
        assert driver.current_ride_id is None

    async def test_cancellation_frees_driver(self, db_session, sample_ride_create):
        driver = DriverORM(id="d6", name="Bia", license_plate="MNO-4", phone="91444", status="available")
        db_session.add(driver)
        await db_session.flush()

        with patch("app.services.ride_service.queue.enqueue_outbox", new_callable=AsyncMock):
            ride = await ride_service.create_ride(db_session, sample_ride_create)

        ride = await ride_service.transition_ride(db_session, ride.id, RideStatus.MATCH, "d6")
        ride = await ride_service.transition_ride(db_session, ride.id, RideStatus.CANCELLED)

        await db_session.refresh(driver)
        assert driver.status == DriverStatus.AVAILABLE


# ==========================================================================
# 5. POLÍTICA DE OVERFLOW — should_delegate
# ==========================================================================

@pytest.mark.asyncio
class TestOverflowPolicy:

    async def test_should_delegate_no_drivers(self, db_session):
        """Sem motoristas disponíveis, deve delegar."""
        result = await ride_service.should_delegate(db_session)
        assert result is True

    async def test_should_not_delegate_with_available_driver(self, db_session):
        """Com motorista disponível, não deve delegar."""
        driver = DriverORM(id="d-overflow", name="Teste", license_plate="OVR-1", phone="00000", status="available")
        db_session.add(driver)
        await db_session.flush()
        result = await ride_service.should_delegate(db_session)
        assert result is False

    async def test_count_available_drivers_zero(self, db_session):
        count = await ride_service.count_available_drivers(db_session)
        assert count == 0

    async def test_count_available_drivers_with_one(self, db_session):
        driver = DriverORM(id="d-count", name="Count", license_plate="CNT-1", phone="11111", status="available")
        db_session.add(driver)
        await db_session.flush()
        count = await ride_service.count_available_drivers(db_session)
        assert count == 1

    async def test_count_excludes_busy_drivers(self, db_session):
        busy = DriverORM(id="d-busy", name="Busy", license_plate="BSY-1", phone="22222", status="busy")
        db_session.add(busy)
        await db_session.flush()
        count = await ride_service.count_available_drivers(db_session)
        assert count == 0

    async def test_overflow_enqueues_to_outbox(self, db_session):
        """Quando não há motoristas, a corrida deve ser enfileirada na saída."""
        sample = RideCreate(passenger_id="p-overflow", origin="X", destination="Y")

        with patch("app.services.ride_service.queue.enqueue_outbox", new_callable=AsyncMock) as mock_enqueue:
            ride = await ride_service.create_ride(db_session, sample)
            mock_enqueue.assert_called_once()
            call_args = mock_enqueue.call_args[0][0]
            assert call_args["id"] == ride.id

    async def test_no_overflow_with_driver_available(self, db_session):
        """Com motorista disponível, NÃO deve enfileirar na saída."""
        driver = DriverORM(id="d-nooverflow", name="Free", license_plate="FREE-1", phone="33333", status="available")
        db_session.add(driver)
        await db_session.flush()

        sample = RideCreate(passenger_id="p-nooverflow", origin="A", destination="B")
        with patch("app.services.ride_service.queue.enqueue_outbox", new_callable=AsyncMock) as mock_enqueue:
            await ride_service.create_ride(db_session, sample)
            mock_enqueue.assert_not_called()


# ==========================================================================
# 6. FILA REDIS — testes unitários com mock
# ==========================================================================

from app.core.queue import (
    enqueue_inbox, enqueue_outbox, ack_inbox, queue_sizes,
    STREAM_INBOX, STREAM_OUTBOX, GROUP_NAME
)


@pytest.mark.asyncio
class TestRedisQueue:

    async def test_enqueue_inbox_calls_xadd(self):
        mock_redis = AsyncMock()
        mock_redis.xgroup_create = AsyncMock(side_effect=Exception("BUSYGROUP"))
        mock_redis.xadd = AsyncMock(return_value="1234-0")
        mock_redis.__aenter__ = AsyncMock(return_value=mock_redis)
        mock_redis.__aexit__ = AsyncMock(return_value=False)

        with patch("app.core.queue.get_redis", return_value=mock_redis):
            msg_id = await enqueue_inbox({"id": "ride-1", "delegated_from": "externo"})

        mock_redis.xadd.assert_called_once()
        call_kwargs = mock_redis.xadd.call_args
        assert call_kwargs[0][0] == STREAM_INBOX

    async def test_enqueue_outbox_calls_xadd(self):
        mock_redis = AsyncMock()
        mock_redis.xgroup_create = AsyncMock(side_effect=Exception("BUSYGROUP"))
        mock_redis.xadd = AsyncMock(return_value="5678-0")
        mock_redis.__aenter__ = AsyncMock(return_value=mock_redis)
        mock_redis.__aexit__ = AsyncMock(return_value=False)

        with patch("app.core.queue.get_redis", return_value=mock_redis):
            msg_id = await enqueue_outbox({"id": "ride-2"})

        mock_redis.xadd.assert_called_once()
        call_args = mock_redis.xadd.call_args
        assert call_args[0][0] == STREAM_OUTBOX

    async def test_queue_sizes_returns_dict(self):
        mock_redis = AsyncMock()
        mock_redis.xlen = AsyncMock(side_effect=[3, 1])
        mock_redis.__aenter__ = AsyncMock(return_value=mock_redis)
        mock_redis.__aexit__ = AsyncMock(return_value=False)

        with patch("app.core.queue.get_redis", return_value=mock_redis):
            sizes = await queue_sizes()

        assert "inbox" in sizes
        assert "outbox" in sizes
        assert sizes["inbox"] == 3
        assert sizes["outbox"] == 1

    async def test_queue_sizes_returns_minus_one_on_error(self):
        mock_redis = AsyncMock()
        mock_redis.xlen = AsyncMock(side_effect=Exception("Redis down"))
        mock_redis.__aenter__ = AsyncMock(return_value=mock_redis)
        mock_redis.__aexit__ = AsyncMock(return_value=False)

        with patch("app.core.queue.get_redis", return_value=mock_redis):
            sizes = await queue_sizes()

        assert sizes["inbox"] == -1
        assert sizes["outbox"] == -1
