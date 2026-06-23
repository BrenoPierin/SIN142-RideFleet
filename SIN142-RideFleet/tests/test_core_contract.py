"""
Testes de CONTRATO com o Core + verificação da exposição de métricas.

Rodam offline (sem o Core no ar). Validam dois lados do contrato:

1. O que o Core CHAMA em nós (callbacks obrigatórios) existe e responde.
2. O que NÓS chamamos no Core tem método, caminho, header e payload no
   formato documentado da API do Core (v0.4.x). Para isso, o httpx é
   substituído por um cliente falso que captura a requisição sem rede.

E verificam que /metrics expõe as séries exigidas pela fase de observabilidade.

Se o Core publicar um OpenAPI/mock oficial, estes testes podem passar a validar
contra ele; enquanto isso, asseguram que o cliente não "deriva" do contrato.
"""
import json

import pytest
from fastapi.testclient import TestClient

import app.core.core_client as cc
from app.main import app


# ── Cliente httpx falso que captura a chamada ────────────────────────────────
class _FakeResp:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class _FakeClient:
    """Registra cada chamada em `calls` e devolve respostas plausíveis do Core."""

    calls = []

    def __init__(self, *a, **k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def _record(self, method, url, **kw):
        _FakeClient.calls.append(
            {
                "method": method,
                "url": url,
                "headers": kw.get("headers") or {},
                "json": kw.get("json"),
            }
        )
        # Respostas mínimas que o core_client espera consumir.
        return _FakeResp(
            {
                "apiKey": "rfk_teste",
                "rideUuid": "11111111-1111-1111-1111-111111111111",
                "logicalTimestamp": 42,
                "locked": True,
            }
        )

    async def post(self, url, **kw):
        return await self._record("POST", url, **kw)

    async def get(self, url, **kw):
        return await self._record("GET", url, **kw)

    async def patch(self, url, **kw):
        return await self._record("PATCH", url, **kw)

    async def request(self, method, url, **kw):
        return await self._record(method, url, **kw)


@pytest.fixture(autouse=True)
def fake_httpx(monkeypatch):
    _FakeClient.calls = []
    monkeypatch.setattr(cc.httpx, "AsyncClient", _FakeClient)
    # Garante base URL e chave determinísticas para as asserções.
    monkeypatch.setattr(cc, "CORE_URL", "http://core:8080/api/v1")
    monkeypatch.setattr(cc, "API_KEY", "rfk_teste")
    monkeypatch.setattr(cc, "GROUP_ID", "ridefleet-grupo-a")
    monkeypatch.setattr(cc, "SERVICE_URL", "http://host.docker.internal:8000")

    # Relógio de Lamport é persistido no Redis; neutraliza para rodar offline.
    async def _tick():
        return 1

    async def _update(ts):
        return int(ts) + 1

    monkeypatch.setattr(cc.lamport, "tick", _tick)
    monkeypatch.setattr(cc.lamport, "update", _update)
    yield


# ── 1. Contrato: o que NÓS chamamos no Core ─────────────────────────────────
async def test_contract_register_group():
    await cc.register_group()
    call = _FakeClient.calls[-1]
    assert call["method"] == "POST"
    assert call["url"] == "http://core:8080/api/v1/groups/register"
    body = call["json"]
    # Campos exigidos pelo contrato de registro do Core.
    for field in ("groupId", "groupName", "serviceUrl", "contactEmail"):
        assert field in body, f"registro sem campo obrigatório: {field}"
    assert body["serviceUrl"].startswith("http")


async def test_contract_create_ride():
    await cc.create_ride_core(
        passenger_id="p1",
        origin={"lat": -20.75, "lng": -42.88},
        destination={"lat": -20.76, "lng": -42.87},
        auction_timeout=10,
    )
    call = _FakeClient.calls[-1]
    assert call["method"] == "POST"
    assert call["url"] == "http://core:8080/api/v1/rides"
    assert call["headers"].get("X-API-Key") == "rfk_teste"
    body = call["json"]
    for field in (
        "originServiceId",
        "passengerId",
        "origin",
        "destination",
        "logicalTimestamp",
        "auctionTimeoutSeconds",
    ):
        assert field in body, f"create_ride sem campo obrigatório: {field}"


async def test_contract_transition_and_locks_use_api_key_and_paths():
    uuid = "22222222-2222-2222-2222-222222222222"
    await cc.transition_ride_core(uuid, "in_transit")
    await cc.acquire_lock(uuid, ttl=60)
    await cc.release_lock(uuid)

    by_url = {(c["method"], c["url"]) for c in _FakeClient.calls}
    assert ("PATCH", f"http://core:8080/api/v1/rides/{uuid}/status") in by_url
    assert ("POST", f"http://core:8080/api/v1/locks/{uuid}") in by_url
    assert ("DELETE", f"http://core:8080/api/v1/locks/{uuid}") in by_url
    # Toda chamada autenticada manda X-API-Key (exceto o registro inicial).
    for c in _FakeClient.calls:
        if c["url"].endswith("/groups/register"):
            continue
        assert c["headers"].get("X-API-Key") == "rfk_teste"


# ── 2. Contrato: o que o Core CHAMA em nós (callbacks obrigatórios) ──────────
def test_callback_endpoints_exist():
    paths = {r.path for r in app.routes}
    methods = {
        r.path: getattr(r, "methods", set()) for r in app.routes
    }
    assert "/rides/incoming" in paths, "callback /rides/incoming ausente"
    assert "POST" in methods["/rides/incoming"]
    assert "/rides/{ride_uuid}/assigned" in paths, "callback /assigned ausente"
    assert "POST" in methods["/rides/{ride_uuid}/assigned"]


# ── 3. Observabilidade: /metrics expõe as séries exigidas ───────────────────
def test_metrics_endpoint_exposes_required_series(monkeypatch):
    # Evita dependência de Redis/Postgres reais no teste de contrato.
    import app.routes.metrics as m

    async def fake_sizes():
        return {"inbox": 0, "outbox": 0}

    monkeypatch.setattr(m, "queue_sizes", fake_sizes)

    with TestClient(app) as client:
        resp = client.get("/metrics")
        assert resp.status_code == 200
        assert "text/plain" in resp.headers["content-type"]
        body = resp.text

    obrigatorias = [
        "ridefleet_rides_local_total",
        "ridefleet_rides_delegated_out_total",
        "ridefleet_rides_received_delegation_total",
        "ridefleet_ride_request_latency_seconds",
        "ridefleet_http_requests_total",
        "ridefleet_service_state",
        "ridefleet_queue_size",
    ]
    for nome in obrigatorias:
        assert nome in body, f"metrica obrigatoria ausente em /metrics: {nome}"
