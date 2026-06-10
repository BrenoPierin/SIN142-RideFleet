"""
Testes da Semana 3 — callbacks do Core e relógio de Lamport.
"""
import pytest
from unittest.mock import AsyncMock, patch
from httpx import AsyncClient, ASGITransport

pytestmark = pytest.mark.asyncio


class TestLamportClock:

    async def test_tick_incrementa_clock(self):
        with patch("app.core.lamport._redis") as mock:
            mock_r = AsyncMock()
            mock_r.__aenter__ = AsyncMock(return_value=mock_r)
            mock_r.__aexit__ = AsyncMock(return_value=False)
            mock_r.incr = AsyncMock(return_value=5)
            mock.return_value = mock_r
            from app.core import lamport
            result = await lamport.tick()
            assert result == 5

    async def test_update_aplica_max_mais_1(self):
        with patch("app.core.lamport._redis") as mock:
            mock_r = AsyncMock()
            mock_r.__aenter__ = AsyncMock(return_value=mock_r)
            mock_r.__aexit__ = AsyncMock(return_value=False)
            mock_r.eval = AsyncMock(return_value=11)  # max(5, 10) + 1
            mock.return_value = mock_r
            from app.core import lamport
            result = await lamport.update(10)
            assert result == 11


class TestIncomingRideEndpoint:

    @pytest.fixture
    async def app_client(self):
        """Cliente de teste com banco SQLite e Redis mockado.

        Estratégia para visibilidade cross-request:
        - Cada request recebe uma nova conexão em modo AUTOCOMMIT via
          engine.connect().execution_options(isolation_level='AUTOCOMMIT').
        - Em AUTOCOMMIT, cada flush() é imediatamente commitado sem precisar
          de session.commit() explícito. Isso é necessário porque pytest-asyncio
          interfere no ciclo de vida do generator dependency do FastAPI, fazendo
          SQLAlchemy devolver a conexão ao pool (com ROLLBACK) antes do commit.
        - A sessão é vinculada à conexão explícita (AsyncSession(bind=conn))
          garantindo que use a conexão autocommit.
        - Arquivo SQLite temporário garante isolamento entre testes.
        """
        import os, tempfile
        from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
        from app.db.database import Base, get_db
        from app.main import app

        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.close()
        db_path = tmp.name

        engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        # Conexão persistente: nunca devolvida ao pool, então sem ROLLBACK
        # implícito do pool entre requests. session.commit() pode operar
        # sobre essa conexão de forma confiável.
        persistent_conn = await engine.connect()

        override_log = []

        async def override_db():
            session = AsyncSession(bind=persistent_conn, expire_on_commit=False)
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise
            finally:
                await session.close()

        app.dependency_overrides[get_db] = override_db

        with patch("app.core.lamport._redis") as mock_redis, \
             patch("app.core.queue.get_redis") as mock_queue:

            mock_r = AsyncMock()
            mock_r.__aenter__ = AsyncMock(return_value=mock_r)
            mock_r.__aexit__ = AsyncMock(return_value=False)
            mock_r.incr = AsyncMock(return_value=1)
            mock_r.eval = AsyncMock(return_value=2)
            mock_r.get  = AsyncMock(return_value="1")
            mock_r.xadd = AsyncMock(return_value="1-0")
            mock_r.xlen = AsyncMock(return_value=0)
            mock_r.xgroup_create = AsyncMock()
            mock_redis.return_value = mock_r
            mock_queue.return_value = mock_r

            async with AsyncClient(
                transport=ASGITransport(app=app),
                base_url="http://test"
            ) as client:
                yield client

        await persistent_conn.close()
        await engine.dispose()
        try:
            os.unlink(db_path)
        except OSError:
            pass
        app.dependency_overrides.clear()

    async def test_incoming_sem_motoristas_retorna_204(self, app_client):
        """Sem motoristas disponíveis, deve recusar com 204."""
        resp = await app_client.post("/rides/incoming", json={
            "rideUuid":        "uuid-test-001",
            "origin":          {"lat": -20.75, "lng": -42.88, "street": "Rua A", "number": "1", "city": "Viçosa", "state": "MG"},
            "destination":     {"lat": -20.80, "lng": -42.90, "street": "Rua B", "number": "2", "city": "Viçosa", "state": "MG"},
            "originServiceId": "grupo-origem",
            "passengerId":     "passageiro-001",
            "logicalTimestamp": 5,
            "auctionDeadline": "2026-05-29T14:05:00Z",
        })
        assert resp.status_code == 204

    async def test_incoming_com_motorista_retorna_proposta(self, app_client):
        """Com motorista disponível, deve retornar proposta com ETA e preço."""
        # Cadastra motorista
        await app_client.post("/drivers/", json={
            "name": "João", "license_plate": "ABC-1234", "phone": "11999"
        })

        resp = await app_client.post("/rides/incoming", json={
            "rideUuid":        "uuid-test-002",
            "origin":          {"lat": -20.75, "lng": -42.88, "street": "Rua A", "number": "1", "city": "Viçosa", "state": "MG"},
            "destination":     {"lat": -20.80, "lng": -42.90, "street": "Rua B", "number": "2", "city": "Viçosa", "state": "MG"},
            "originServiceId": "grupo-origem",
            "passengerId":     "passageiro-002",
            "logicalTimestamp": 5,
            "auctionDeadline": "2026-05-29T14:05:00Z",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert "estimatedEta"   in data
        assert "estimatedPrice" in data
        assert "logicalTimestamp" in data
        assert data["estimatedEta"] > 0
        assert data["estimatedPrice"] > 0

    async def test_assigned_enfileira_na_inbox(self, app_client):
        """Notificação de vitória deve enfileirar corrida na inbox."""
        resp = await app_client.post("/rides/uuid-test-003/assigned", json={
            "rideUuid":        "uuid-test-003",
            "origin":          {"lat": -20.75, "lng": -42.88, "street": "Rua A", "number": "1", "city": "Viçosa", "state": "MG"},
            "destination":     {"lat": -20.80, "lng": -42.90, "street": "Rua B", "number": "2", "city": "Viçosa", "state": "MG"},
            "passengerId":     "passageiro-003",
            "originServiceId": "grupo-origem",
            "logicalTimestamp": 8,
            "lockExpiresAt":   "2026-05-29T14:06:00Z",
        })
        assert resp.status_code == 200
        assert resp.json()["received"] is True

    async def test_audit_endpoint_retorna_200(self, app_client):
        """Endpoint de auditoria deve retornar 200 (ou 502 se Core offline)."""
        with patch("app.core.core_client.get_ride_audit", new_callable=AsyncMock) as mock_audit:
            mock_audit.return_value = {"events": []}
            resp = await app_client.get("/audit/rides/uuid-test-001")
            assert resp.status_code == 200

    async def test_clock_endpoint(self, app_client):
        """Endpoint /audit/clock deve retornar o clock atual."""
        resp = await app_client.get("/audit/clock")
        assert resp.status_code == 200
        assert "clock" in resp.json()

    async def test_assigned_enfileira_na_inbox(self, app_client):
        """Notificação de vitória deve enfileirar corrida na inbox."""
        resp = await app_client.post("/rides/uuid-test-003/assigned", json={
            "rideUuid":        "uuid-test-003",
            "origin":          {"lat": -20.75, "lng": -42.88, "street": "Rua A", "number": "1", "city": "Viçosa", "state": "MG"},
            "destination":     {"lat": -20.80, "lng": -42.90, "street": "Rua B", "number": "2", "city": "Viçosa", "state": "MG"},
            "passengerId":     "passageiro-003",
            "originServiceId": "grupo-origem",
            "logicalTimestamp": 8,
            "lockExpiresAt":   "2026-05-29T14:06:00Z",
        })
        assert resp.status_code == 200
        assert resp.json()["received"] is True

    async def test_audit_endpoint_retorna_200(self, app_client):
        """Endpoint de auditoria deve retornar 200 (ou 502 se Core offline)."""
        with patch("app.core.core_client.get_ride_audit", new_callable=AsyncMock) as mock_audit:
            mock_audit.return_value = {"events": []}
            resp = await app_client.get("/audit/rides/uuid-test-001")
            assert resp.status_code == 200

    async def test_clock_endpoint(self, app_client):
        """Endpoint /audit/clock deve retornar o clock atual."""
        resp = await app_client.get("/audit/clock")
        assert resp.status_code == 200
        assert "clock" in resp.json()
