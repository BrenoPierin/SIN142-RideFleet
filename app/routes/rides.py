from fastapi import APIRouter, HTTPException
from app.models.ride import Ride, RideCreate, RideTransition
from app.services import ride_service

router = APIRouter(prefix="/rides", tags=["rides"])


@router.post("/", response_model=Ride, status_code=201)
def create_ride(data: RideCreate):
    """Passageiro solicita uma corrida."""
    return ride_service.create_ride(data)


@router.get("/", response_model=list[Ride])
def list_rides():
    """Lista todas as corridas."""
    return ride_service.list_rides()


@router.get("/pending", response_model=list[Ride])
def list_pending_rides():
    """Lista corridas aguardando atribuição de motorista."""
    return ride_service.get_pending_rides()


@router.get("/{ride_id}", response_model=Ride)
def get_ride(ride_id: str):
    """Retorna detalhes de uma corrida."""
    ride = ride_service.get_ride(ride_id)
    if not ride:
        raise HTTPException(status_code=404, detail="Corrida não encontrada.")
    return ride


@router.patch("/{ride_id}/status", response_model=Ride)
def transition_ride(ride_id: str, body: RideTransition):
    """
    Aplica uma transição de estado na corrida.
    Exemplo: REQUEST -> MATCH (atribuindo motorista)
    """
    try:
        return ride_service.transition_ride(ride_id, body.new_status, body.driver_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/overflow/check")
def check_overflow():
    """
    Verifica se o serviço está congestionado e deve delegar corridas.
    Usado pelo Core para decidir se inicia um leilão.
    """
    should_delegate = ride_service.should_delegate()
    available = ride_service.get_available_drivers()
    return {
        "should_delegate": should_delegate,
        "available_drivers": len(available),
        "pending_rides": len(ride_service.get_pending_rides()),
    }
