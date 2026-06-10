"""
Logging estruturado em JSON para o RideFleet.
Todo evento significativo gera um log com os campos exigidos pelo Core:
  timestamp, evento, corrida_id, servico_origem, estado_anterior, estado_novo

Níveis:
  INFO  — fluxo normal
  WARN  — degradação (fila cheia, motoristas escassos)
  ERROR — falha real
"""
import logging
import json
import os
from datetime import datetime, timezone

SERVICE_NAME = os.getenv("SERVICE_NAME", "ridefleet")


class JSONFormatter(logging.Formatter):
    """Formata cada log como uma linha JSON."""

    def format(self, record: logging.LogRecord) -> str:
        base = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "service": SERVICE_NAME,
            "message": record.getMessage(),
        }
        # Campos extras passados via extra={}
        for key in ("evento", "corrida_id", "servico_origem", "estado_anterior", "estado_novo"):
            if hasattr(record, key):
                base[key] = getattr(record, key)

        if record.exc_info:
            base["exception"] = self.formatException(record.exc_info)

        return json.dumps(base, ensure_ascii=False)


def get_logger(name: str = SERVICE_NAME) -> logging.Logger:
    logger = logging.getLogger(name)
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(JSONFormatter())
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
    return logger


# Logger padrão do serviço
logger = get_logger()


# --- Funções de conveniência para eventos de corrida ---

def log_ride_event(
    evento: str,
    corrida_id: str,
    estado_anterior: str | None = None,
    estado_novo: str | None = None,
    servico_origem: str = SERVICE_NAME,
    nivel: str = "INFO",
    **extra,
):
    """
    Loga um evento de corrida com todos os campos obrigatórios do Core.

    Exemplo:
        log_ride_event("corrida_criada", ride.id, estado_novo="request")
        log_ride_event("transicao_estado", ride.id, "request", "match")
    """
    log_extra = {
        "evento": evento,
        "corrida_id": corrida_id,
        "servico_origem": servico_origem,
        "estado_anterior": estado_anterior,
        "estado_novo": estado_novo,
        **extra,
    }

    msg = f"[{evento}] corrida={corrida_id}"
    if estado_anterior and estado_novo:
        msg += f" | {estado_anterior} → {estado_novo}"

    if nivel == "WARN":
        logger.warning(msg, extra=log_extra)
    elif nivel == "ERROR":
        logger.error(msg, extra=log_extra)
    else:
        logger.info(msg, extra=log_extra)
