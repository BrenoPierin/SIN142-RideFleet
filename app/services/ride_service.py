"""
Lógica de negócio central das corridas.
Separa as regras de negócio das rotas HTTP.
"""
from typing import Optional
from app.models.ride import Ride, RideCreate, RideStatus
from app.models.driver import Driver, DriverStatus
from app.db import database as db


# Política de overflow: quantos motoristas livres mínimos antes de delegar
MIN_AVAILABLE_DRIVERS = 1


def create_ride(data: RideCreate) -> Ride:
    """Cria uma nova corrida no estado REQUEST."""
    ride = Ride(**data.model_dump())
    db.rides[ride.id] = ride
    return ride


def get_ride(ride_id: str) -> Optional[Ride]:
    return db.rides.get(ride_id)


def list_rides() -> list[Ride]:
    return list(db.rides.values())


def transition_ride(ride_id: str, new_status: RideStatus, driver_id: Optional[str] = None) -> Ride:
    """Aplica uma transição de estado na corrida."""
    ride = db.rides.get(ride_id)
    if not ride:
        raise ValueError(f"Corrida {ride_id} não encontrada.")

    ride.transition(new_status, driver_id)

    # Se um motorista foi atribuído, marca ele como BUSY
    if driver_id and new_status == RideStatus.MATCH:
        driver = db.drivers.get(driver_id)
        if driver:
            driver.status = DriverStatus.BUSY
            driver.current_ride_id = ride_id

    # Se a corrida foi concluída ou cancelada, libera o motorista
    if new_status in (RideStatus.COMPLETE, RideStatus.CANCELLED):
        if ride.driver_id:
            driver = db.drivers.get(ride.driver_id)
            if driver:
                driver.status = DriverStatus.AVAILABLE
                driver.current_ride_id = None

    return ride


def get_available_drivers() -> list[Driver]:
    """Retorna todos os motoristas disponíveis."""
    return [d for d in db.drivers.values() if d.status == DriverStatus.AVAILABLE]


def should_delegate() -> bool:
    """
    Política de overflow: decide se o serviço está congestionado
    e deve delegar a corrida para outro grupo via Core.
    Critério atual: menos de MIN_AVAILABLE_DRIVERS motoristas livres.
    """
    available = len(get_available_drivers())
    return available < MIN_AVAILABLE_DRIVERS


def get_pending_rides() -> list[Ride]:
    """Corridas no estado REQUEST aguardando atribuição."""
    return [r for r in db.rides.values() if r.status == RideStatus.REQUEST]
