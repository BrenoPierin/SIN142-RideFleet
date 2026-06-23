"""
Serviço de conexão com o Core.

Responsável por OBTER AUTOMATICAMENTE a API Key:
o backend chama POST /groups/register repetidamente (com backoff) até o Core
responder — útil quando o Core ainda está subindo ou ficou offline.

Usado em dois lugares:
  - main.py (startup): dispara `auto_register_loop()` em background.
  - routes/core_webhook.py: o webhook POST /core/register chama
    `register_once()` para forçar uma tentativa sob demanda.
"""
import os
import asyncio

from app.core import core_client
from app.core.logging import logger

# Parâmetros do backoff exponencial (limitado).
BASE_DELAY = float(os.getenv("CORE_REGISTER_BASE_DELAY", "3"))
MAX_DELAY  = float(os.getenv("CORE_REGISTER_MAX_DELAY", "60"))
# 0 = tentar para sempre. Caso contrário, número máximo de tentativas.
MAX_ATTEMPTS = int(os.getenv("CORE_REGISTER_MAX_ATTEMPTS", "0"))


async def register_once() -> dict:
    """
    Faz UMA tentativa de registro. Não levanta exceção — devolve um dict
    com o resultado, adequado para responder a um webhook HTTP.
    """
    try:
        key = await core_client.register_group()
        return {
            "ok": True,
            "connected": True,
            "apiKeyMasked": core_client.connection_status["apiKeyMasked"],
            "groupId": core_client.GROUP_ID,
            "coreUrl": core_client.CORE_URL,
        }
    except Exception as e:
        logger.error(
            f"Tentativa de registro no Core falhou: {e}",
            extra={"evento": "core_register_failed"},
        )
        return {
            "ok": False,
            "connected": core_client.is_connected(),
            "error": str(e),
            "groupId": core_client.GROUP_ID,
            "coreUrl": core_client.CORE_URL,
        }


async def auto_register_loop() -> None:
    """
    Loop de fundo: insiste no registro até conseguir a API Key.

    - Se a chave já veio do .env/disco, ainda assim revalida uma vez no
      startup (idempotente) para garantir que o Core conhece nosso serviceUrl.
    - Em caso de falha, espera com backoff exponencial (até MAX_DELAY) e
      tenta de novo. Roda silenciosamente como task até ter sucesso.
    """
    attempt = 0
    while True:
        attempt += 1
        try:
            key = await core_client.register_group()
            logger.info(
                f"Conectado ao Core (tentativa {attempt}). "
                f"API Key: {core_client._mask(key)}",
                extra={"evento": "core_connected"},
            )
            return
        except Exception as e:
            if MAX_ATTEMPTS and attempt >= MAX_ATTEMPTS:
                logger.error(
                    f"Desisti de registrar no Core após {attempt} tentativas: {e}",
                    extra={"evento": "core_register_giveup"},
                )
                return
            delay = min(MAX_DELAY, BASE_DELAY * attempt)
            logger.warning(
                f"Core indisponível (tentativa {attempt}): {e}. "
                f"Nova tentativa em {delay:.0f}s.",
                extra={"evento": "core_register_retry"},
            )
            await asyncio.sleep(delay)
