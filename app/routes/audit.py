"""
Endpoint de auditoria — expõe o log causal de uma corrida.
Busca os eventos no Core e retorna em ordem de logicalTimestamp.
"""
from fastapi import APIRouter, HTTPException
from app.core import core_client, lamport

router = APIRouter(prefix="/audit", tags=["audit"])


@router.get("/rides/{ride_uuid}")
async def get_causal_log(ride_uuid: str):
    """
    Retorna o log causal completo de uma corrida.
    Inclui todos os eventos com timestamps de Lamport,
    mesmo os que ocorreram em outros serviços.
    """
    try:
        audit = await core_client.get_ride_audit(ride_uuid)
        clock = await lamport.current()
        return {
            "ride_uuid":     ride_uuid,
            "local_clock":   clock,
            "causal_log":    audit,
        }
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Core inacessível: {e}")


@router.get("/rides/{ride_uuid}/status")
async def get_ride_core_status(ride_uuid: str):
    """Consulta o estado atual da saga e do lock no Core."""
    try:
        return await core_client.get_ride_status(ride_uuid)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Core inacessível: {e}")


@router.get("/rides/{ride_uuid}/proposals")
async def get_ride_proposals(ride_uuid: str):
    """Retorna o resultado do leilão — propostas e vencedor."""
    try:
        return await core_client.get_ride_proposals(ride_uuid)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Core inacessível: {e}")


@router.get("/clock")
async def get_lamport_clock():
    """Retorna o valor atual do relógio de Lamport deste serviço."""
    return {"clock": await lamport.current()}
