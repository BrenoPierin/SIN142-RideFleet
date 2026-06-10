"""
Entrypoint FastAPI — Semana 3.
Adicionado:
  - Auto-registro no Core no startup
  - Workers de inbox e delegation em background
  - Relógio de Lamport inicializado
  - Callbacks do Core registrados
"""
import asyncio
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.db.database import create_tables
from app.routes import rides, drivers, passengers, health
from app.routes.core_callbacks import router as callbacks_router
from app.routes.audit import router as audit_router
from app.core import core_client
from app.services.inbox_worker import inbox_worker
from app.services.delegation_service import delegation_worker
from app.services.monitor import alert_monitor
from app.core.logging import logger


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Iniciando RideFleet...", extra={"evento": "startup"})

    # Cria tabelas no banco
    await create_tables()

    # Registra o grupo no Core (idempotente)
    try:
        api_key = await core_client.register_group()
        logger.info(f"Registrado no Core. API Key: {api_key[:8]}...", extra={"evento": "core_registered"})
    except Exception as e:
        logger.error(f"Falha ao registrar no Core: {e}. Continuando...", extra={"evento": "core_register_failed"})

    # Inicia workers em background
    inbox_task      = asyncio.create_task(inbox_worker())
    delegation_task = asyncio.create_task(delegation_worker())
    monitor_task    = asyncio.create_task(alert_monitor())

    logger.info("Workers iniciados.", extra={"evento": "workers_started"})

    yield

    # Cancela workers no shutdown
    inbox_task.cancel()
    delegation_task.cancel()
    monitor_task.cancel()
    logger.info("Encerrando RideFleet.", extra={"evento": "shutdown"})


app = FastAPI(
    title="RideFleet — Serviço de Transporte",
    description="SIN 142 — Sistemas Distribuídos UFV 2026/1",
    version="0.3.0",
    lifespan=lifespan,
)

# CORS — permite que o front-end (navegador) consuma a API.
# allow_credentials=False permite usar "*" em allow_origins; estes endpoints
# nao usam cookies/sessao. Em producao, restrinja allow_origins as origens reais.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Rotas existentes
app.include_router(health.router)
app.include_router(rides.router)
app.include_router(drivers.router)
app.include_router(passengers.router)

# Novas rotas da Semana 3
app.include_router(callbacks_router)   # /rides/incoming e /rides/{uuid}/assigned
app.include_router(audit_router)       # /audit/rides/{uuid}


@app.get("/")
def root():
    return {"service": "RideFleet", "version": "0.3.0", "status": "online"}