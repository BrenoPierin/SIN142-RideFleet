from fastapi import FastAPI
from app.routes import rides, drivers, passengers

app = FastAPI(
    title="RideFleet — Serviço de Transporte",
    description="SIN 142 — Sistemas Distribuídos UFV 2026/1",
    version="0.1.0",
)

# Registra as rotas
app.include_router(rides.router)
app.include_router(drivers.router)
app.include_router(passengers.router)


@app.get("/")
def root():
    return {"service": "RideFleet", "status": "online"}


@app.get("/health")
def health():
    """
    Health check básico — será expandido na Semana 2
    com status detalhado, tamanho da fila e latência.
    """
    from app.db import database as db
    from app.models.driver import DriverStatus
    available = sum(1 for d in db.drivers.values() if d.status == DriverStatus.AVAILABLE)
    return {
        "status": "UP",
        "available_drivers": available,
        "total_rides": len(db.rides),
    }
