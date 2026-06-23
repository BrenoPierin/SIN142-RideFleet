"""
Entrypoint FastAPI — Semana 3 (+ integração automática da API Key do Core).

Mudanças nesta versão:
  - O registro no Core agora roda em BACKGROUND com retry/backoff
    (app.services.core_connection.auto_register_loop), em vez de uma única
    tentativa bloqueante no startup. Se o Core estiver fora do ar, o backend
    sobe normalmente e continua tentando obter a API Key sozinho.
  - Novo router /core (webhook): POST /core/register, GET /core/status,
    POST /core/webhook/api-key.
"""
import asyncio
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.db.database import create_tables
from app.routes import rides, drivers, passengers, health
from app.routes.core_callbacks import router as callbacks_router
from app.routes.audit import router as audit_router
from app.routes.metrics import router as metrics_router
from app.routes.core_webhook import router as core_webhook_router
from app.core.metrics import PrometheusMiddleware
from app.services.core_connection import auto_register_loop
from app.services.inbox_worker import inbox_worker
from app.services.delegation_service import delegation_worker
from app.services.monitor import alert_monitor
from app.core.logging import logger


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Iniciando RideFleet...", extra={"evento": "startup"})

    # Cria tabelas no banco
    await create_tables()

    # Solicita a API Key ao Core em background (não bloqueia o boot).
    # Continua tentando com backoff até conseguir — ou até o webhook
    # POST /core/register ser chamado manualmente.
    core_task = asyncio.create_task(auto_register_loop())

    # Inicia workers em background
    inbox_task      = asyncio.create_task(inbox_worker())
    delegation_task = asyncio.create_task(delegation_worker())
    monitor_task    = asyncio.create_task(alert_monitor())

    logger.info("Workers iniciados.", extra={"evento": "workers_started"})

    yield

    # Cancela tasks no shutdown
    for task in (core_task, inbox_task, delegation_task, monitor_task):
        task.cancel()
    logger.info("Encerrando RideFleet.", extra={"evento": "shutdown"})


app = FastAPI(
    title="RideFleet — Serviço de Transporte",
    description="SIN 142 — Sistemas Distribuídos UFV 2026/1",
    version="0.4.0",
    lifespan=lifespan,
)

# CORS — permite que o front-end (navegador) consuma a API.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Observabilidade — mede throughput e latência de cada requisição.
app.add_middleware(PrometheusMiddleware)

# Rotas existentes
app.include_router(health.router)
app.include_router(rides.router)
app.include_router(drivers.router)
app.include_router(passengers.router)

# Rotas da Semana 3
app.include_router(callbacks_router)       # /rides/incoming e /rides/{uuid}/assigned
app.include_router(audit_router)           # /audit/rides/{uuid}
app.include_router(metrics_router)         # /metrics (Prometheus)

# Integração com o Core (API Key)
app.include_router(core_webhook_router)    # /core/register, /core/status, /core/webhook/api-key


@app.get("/")
def root():
    return {"service": "RideFleet", "version": "0.4.0", "status": "online"}
