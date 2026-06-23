"""
Webhook de integração com o Core — gestão da API Key.

Endpoints:
  POST /core/register          — dispara o registro AGORA e devolve o status.
                                 (o backend "solicita" a API Key ao Core)
  GET  /core/status            — estado atual da conexão (chave mascarada).
  POST /core/webhook/api-key   — entrada opcional: o Core (ou um operador)
                                 EMPURRA uma API Key para o backend.

Notas de segurança:
  - A chave nunca é devolvida em texto puro pelos GETs; apenas mascarada.
  - O push (/core/webhook/api-key) é protegido por um segredo compartilhado
    em CORE_WEBHOOK_SECRET (header X-Webhook-Secret). Se a env não estiver
    definida, o push fica DESABILITADO (retorna 503) para não virar um vetor
    de injeção de chave.
"""
import os
from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel
from typing import Optional

from app.core import core_client
from app.services.core_connection import register_once
from app.core.logging import logger

router = APIRouter(prefix="/core", tags=["core-integration"])

WEBHOOK_SECRET = os.getenv("CORE_WEBHOOK_SECRET", "")


class ApiKeyPush(BaseModel):
    apiKey: str


@router.post("/register")
async def trigger_register():
    """
    Solicita (ou re-solicita) a API Key ao Core imediatamente.
    Idempotente: se o grupo já existe, o Core devolve a mesma chave.
    """
    result = await register_once()
    if not result["ok"] and not result["connected"]:
        # Não conseguimos a chave e não temos nenhuma em cache.
        raise HTTPException(status_code=502, detail=result)
    return result


@router.get("/status")
async def core_status():
    """Estado da conexão com o Core (sem expor a chave em texto puro)."""
    return core_client.connection_status


@router.post("/webhook/api-key")
async def receive_api_key(
    payload: ApiKeyPush,
    x_webhook_secret: Optional[str] = Header(default=None),
):
    """
    Recebe uma API Key empurrada pelo Core/operador.
    Requer header X-Webhook-Secret == CORE_WEBHOOK_SECRET.
    """
    if not WEBHOOK_SECRET:
        raise HTTPException(
            status_code=503,
            detail="Push de API Key desabilitado (defina CORE_WEBHOOK_SECRET).",
        )
    if x_webhook_secret != WEBHOOK_SECRET:
        raise HTTPException(status_code=401, detail="Segredo do webhook inválido.")

    core_client.set_api_key(payload.apiKey)
    logger.info(
        "API Key recebida via webhook e persistida.",
        extra={"evento": "core_key_pushed"},
    )
    return {
        "received": True,
        "connected": True,
        "apiKeyMasked": core_client.connection_status["apiKeyMasked"],
    }
