"""
Métricas Prometheus do RideFleet — Fase de Observabilidade.

Expostas em GET /metrics no formato de exposição Prometheus. Cobrem tudo que
o dashboard Grafana do Core consome:

  - ridefleet_rides_local_total            corridas atendidas localmente
  - ridefleet_rides_delegated_out_total    corridas delegadas para fora (overflow)
  - ridefleet_rides_received_delegation_total  corridas recebidas de outros grupos
  - ridefleet_ride_request_latency_seconds histograma de latência dos endpoints /rides
  - ridefleet_http_requests_total          contador p/ throughput (use rate())
  - ridefleet_service_state                0=disponivel, 1=congestionado, 2=fora_do_ar
  - ridefleet_queue_size{queue=inbox|outbox}   tamanho atual das filas
  - ridefleet_available_drivers            motoristas livres
  - ridefleet_build_info                   metadados da instância (versão, etc.)

Distribuição de carga entre instâncias: todas as séries carregam o label
`service_instance` (nome do container). Isso permite agrupar a carga por
instância no Grafana mesmo quando o scrape passa pelo load balancer. Evitamos
de propósito os labels reservados `instance` e `job`, que o Prometheus injeta
no momento do scrape.

Premissa: 1 worker por container. Cada instância (ridefleet-grupo-a-1,
ridefleet-grupo-a-2) é um alvo de scrape independente.
"""
import os
import re
import time

from prometheus_client import (
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
    CONTENT_TYPE_LATEST,
)
from starlette.middleware.base import BaseHTTPMiddleware

SERVICE_NAME = os.getenv("SERVICE_NAME", "ridefleet")
INSTANCE_ID = os.getenv("INSTANCE_ID") or os.getenv("HOSTNAME") or "local"
SERVICE_VERSION = os.getenv("SERVICE_VERSION", "0.4.0")

# Registry próprio — evita colisão com coletores default ao reimportar (testes).
REGISTRY = CollectorRegistry()

# Labels comuns a todas as séries.
_C = {"service": SERVICE_NAME, "service_instance": INSTANCE_ID}

# ── Corridas ────────────────────────────────────────────────────────────────
rides_local_total = Counter(
    "ridefleet_rides_local_total",
    "Corridas atendidas localmente (motorista do proprio grupo).",
    ["service", "service_instance"],
    registry=REGISTRY,
)
rides_delegated_out_total = Counter(
    "ridefleet_rides_delegated_out_total",
    "Corridas delegadas para fora via Core (overflow).",
    ["service", "service_instance"],
    registry=REGISTRY,
)
rides_received_delegation_total = Counter(
    "ridefleet_rides_received_delegation_total",
    "Corridas recebidas por delegacao de outros grupos (leilao vencido).",
    ["service", "service_instance", "origin_service"],
    registry=REGISTRY,
)

# ── HTTP: throughput + latência ───────────────────────────────────────────────
http_requests_total = Counter(
    "ridefleet_http_requests_total",
    "Total de requisicoes HTTP. Throughput = rate(ridefleet_http_requests_total[1m]).",
    ["service", "service_instance", "method", "endpoint", "status"],
    registry=REGISTRY,
)
ride_request_latency = Histogram(
    "ridefleet_ride_request_latency_seconds",
    "Latencia dos endpoints de corrida, em segundos.",
    ["service", "service_instance", "method", "endpoint"],
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0),
    registry=REGISTRY,
)

# ── Estado / filas / motoristas (gauges atualizados no scrape) ────────────────
service_state = Gauge(
    "ridefleet_service_state",
    "Estado do servico: 0=disponivel, 1=congestionado, 2=fora_do_ar.",
    ["service", "service_instance"],
    registry=REGISTRY,
)
queue_size = Gauge(
    "ridefleet_queue_size",
    "Tamanho atual das filas de entrada (inbox) e saida (outbox).",
    ["service", "service_instance", "queue"],
    registry=REGISTRY,
)
available_drivers = Gauge(
    "ridefleet_available_drivers",
    "Motoristas disponiveis no momento.",
    ["service", "service_instance"],
    registry=REGISTRY,
)
build_info = Gauge(
    "ridefleet_build_info",
    "Metadados da instancia (sempre 1). Use os labels para identificar a build.",
    ["service", "service_instance", "version"],
    registry=REGISTRY,
)
build_info.labels(**_C, version=SERVICE_VERSION).set(1)


# ── Helpers de incremento ──────────────────────────────────────────────────
def inc_local_ride() -> None:
    rides_local_total.labels(**_C).inc()


def inc_delegated_out() -> None:
    rides_delegated_out_total.labels(**_C).inc()


def inc_received_delegation(origin_service: str = "externo") -> None:
    rides_received_delegation_total.labels(
        **_C, origin_service=origin_service or "externo"
    ).inc()


def set_runtime_gauges(state: int, inbox: int, outbox: int, drivers: int) -> None:
    """Atualiza os gauges de ponto-no-tempo. Chamado no handler de /metrics."""
    service_state.labels(**_C).set(state)
    queue_size.labels(**_C, queue="inbox").set(inbox)
    queue_size.labels(**_C, queue="outbox").set(outbox)
    available_drivers.labels(**_C).set(drivers)


# ── Normalização de endpoint (limita cardinalidade) ──────────────────────────
_UUID_RE = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)
_NUM_RE = re.compile(r"^\d+$")


def normalize_endpoint(path: str) -> str:
    """/rides/<uuid>/status -> /rides/{id}/status (evita explosao de labels)."""
    out = []
    for seg in path.split("/"):
        if seg and (_UUID_RE.fullmatch(seg) or _NUM_RE.match(seg)):
            out.append("{id}")
        else:
            out.append(seg)
    return "/".join(out) or "/"


def observe_request(method: str, path: str, status: int, duration_s: float) -> None:
    endpoint = normalize_endpoint(path)
    http_requests_total.labels(
        **_C, method=method, endpoint=endpoint, status=str(status)
    ).inc()
    # Latência detalhada apenas para os endpoints de corrida.
    if endpoint == "/rides" or endpoint.startswith("/rides/"):
        ride_request_latency.labels(
            **_C, method=method, endpoint=endpoint
        ).observe(duration_s)


def render() -> bytes:
    return generate_latest(REGISTRY)


# ── Middleware ───────────────────────────────────────────────────────────────
class PrometheusMiddleware(BaseHTTPMiddleware):
    """Mede throughput e latência de toda requisição (exceto o próprio /metrics)."""

    async def dispatch(self, request, call_next):
        path = request.url.path
        if path == "/metrics":
            return await call_next(request)
        start = time.perf_counter()
        status = 500
        try:
            response = await call_next(request)
            status = response.status_code
            return response
        finally:
            observe_request(
                request.method, path, status, time.perf_counter() - start
            )
