from contextlib import asynccontextmanager
from fastapi import FastAPI
from app.db.database import create_tables
from app.routes import rides, drivers, passengers, health
from app.core.logging import logger


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Executado na inicialização e encerramento da aplicação."""
    logger.info("Iniciando RideFleet...", extra={"evento": "startup"})
    await create_tables()
    logger.info("Tabelas criadas/verificadas.", extra={"evento": "startup"})
    yield
    logger.info("Encerrando RideFleet.", extra={"evento": "shutdown"})


app = FastAPI(
    title="RideFleet — Serviço de Transporte",
    description="SIN 142 — Sistemas Distribuídos UFV 2026/1",
    version="0.2.0",
    lifespan=lifespan,
)

app.include_router(health.router)
app.include_router(rides.router)
app.include_router(drivers.router)
app.include_router(passengers.router)


@app.get("/")
def root():
    return {"service": "RideFleet", "version": "0.2.0", "status": "online"}
