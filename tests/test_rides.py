"""
Testes unitários da lógica de negócio — Semana 1.
Execute com: pytest tests/ -v
"""
import pytest
from app.models.ride import Ride, RideCreate, RideStatus
from app.models.driver import Driver, DriverCreate
from app.models.passenger import Passenger, PassengerCreate
from app.services import ride_service
from app.db import database as db


@pytest.fixture(autouse=True)
def limpar_banco():
    """Limpa o banco em memória antes de cada teste."""
    db.rides.clear()
    db.drivers.clear()
    db.passengers.clear()
    yield


# --- Testes da máquina de estados ---

def test_corrida_criada_com_status_request():
    ride = Ride(passenger_id="p1", origin="A", destination="B")
    assert ride.status == RideStatus.REQUEST


def test_transicao_valida_request_para_match():
    ride = Ride(passenger_id="p1", origin="A", destination="B")
    ride.transition(RideStatus.MATCH, driver_id="d1")
    assert ride.status == RideStatus.MATCH
    assert ride.driver_id == "d1"


def test_transicao_invalida_levanta_excecao():
    ride = Ride(passenger_id="p1", origin="A", destination="B")
    with pytest.raises(ValueError):
        ride.transition(RideStatus.COMPLETE)  # Pula etapas — inválido


def test_fluxo_completo_happy_path():
    ride = Ride(passenger_id="p1", origin="A", destination="B")
    ride.transition(RideStatus.MATCH, driver_id="d1")
    ride.transition(RideStatus.CONFIRM)
    ride.transition(RideStatus.IN_TRANSIT)
    ride.transition(RideStatus.COMPLETE)
    assert ride.status == RideStatus.COMPLETE


def test_cancelamento_em_qualquer_estado():
    for status in [RideStatus.REQUEST, RideStatus.MATCH, RideStatus.CONFIRM, RideStatus.IN_TRANSIT]:
        ride = Ride(passenger_id="p1", origin="A", destination="B")
        ride.status = status
        ride.transition(RideStatus.CANCELLED)
        assert ride.status == RideStatus.CANCELLED


# --- Testes do serviço ---

def test_criar_corrida_via_servico():
    data = RideCreate(passenger_id="p1", origin="Rua A", destination="Rua B")
    ride = ride_service.create_ride(data)
    assert ride.id in db.rides
    assert ride.status == RideStatus.REQUEST


def test_overflow_sem_motoristas():
    """Com 0 motoristas disponíveis, deve sugerir delegação."""
    assert ride_service.should_delegate() is True


def test_sem_overflow_com_motorista_disponivel():
    driver = Driver(name="João", license_plate="ABC-1234", phone="11999999999")
    db.drivers[driver.id] = driver
    assert ride_service.should_delegate() is False


def test_motorista_fica_busy_apos_match():
    driver = Driver(name="João", license_plate="ABC-1234", phone="11999999999")
    db.drivers[driver.id] = driver

    data = RideCreate(passenger_id="p1", origin="A", destination="B")
    ride = ride_service.create_ride(data)
    ride_service.transition_ride(ride.id, RideStatus.MATCH, driver_id=driver.id)

    assert db.drivers[driver.id].status.value == "busy"


def test_motorista_fica_disponivel_apos_complete():
    from app.models.driver import DriverStatus
    driver = Driver(name="João", license_plate="ABC-1234", phone="11999999999")
    db.drivers[driver.id] = driver

    data = RideCreate(passenger_id="p1", origin="A", destination="B")
    ride = ride_service.create_ride(data)
    ride_service.transition_ride(ride.id, RideStatus.MATCH, driver_id=driver.id)
    ride_service.transition_ride(ride.id, RideStatus.CONFIRM)
    ride_service.transition_ride(ride.id, RideStatus.IN_TRANSIT)
    ride_service.transition_ride(ride.id, RideStatus.COMPLETE)

    assert db.drivers[driver.id].status == DriverStatus.AVAILABLE
