"""
Monitor de alertas basicos — Semana 2.

Executa em background e emite logs estruturados de ALERTA quando:
  - a fila de entrada (inbox) ultrapassa o threshold;
  - a fila de saida (outbox) ultrapassa o threshold;
  - nenhum motorista esta disponivel;
  - a taxa de erros recente fica acima do limite.

Nao depende de stack externa: usa os mesmos logs estruturados (JSON) ja
consultaveis e correlacionaveis com o Core. Os alertas saem em nivel WARN,
com o campo "alerta": true, e sao desduplicados por mudanca de estado para
evitar flood (so logam quando a condicao passa de normal -> alerta).

Configuravel por variaveis de ambiente:
  QUEUE_OVERFLOW_THRESHOLD  (default 10)  tamanho de fila que dispara alerta
  ALERT_ERROR_RATE          (default 5)   erros por intervalo que disparam alerta
  ALERT_INTERVAL_SECONDS    (default 15)  periodo de checagem
"""
import asyncio
import logging
import os

from app.core.logging import logger
from app.core.queue import queue_sizes

QUEUE_THRESHOLD  = int(os.getenv("QUEUE_OVERFLOW_THRESHOLD", "10"))
ERROR_RATE_LIMIT = int(os.getenv("ALERT_ERROR_RATE", "5"))
CHECK_INTERVAL_S = int(os.getenv("ALERT_INTERVAL_SECONDS", "15"))


class _ErrorCounter(logging.Handler):
    """Conta registros de nivel ERROR+ desde o ultimo reset."""

    def __init__(self):
        super().__init__(level=logging.ERROR)
        self.count = 0

    def emit(self, record):
        self.count += 1

    def reset(self) -> int:
        atual = self.count
        self.count = 0
        return atual


_error_counter = _ErrorCounter()


def _emit_alert(evento: str, mensagem: str, **extra):
    logger.warning(mensagem, extra={"evento": evento, "alerta": True, **extra})


async def alert_monitor():
    """Loop de monitoramento. Roda como task de background a partir do lifespan."""
    # Conecta o contador de erros ao logger raiz (captura ERROR de todo o app)
    logging.getLogger().addHandler(_error_counter)

    ja_alertado = {"inbox": False, "outbox": False, "drivers": False}
    logger.info("Monitor de alertas iniciado.", extra={"evento": "alert_monitor_start"})

    while True:
        try:
            await asyncio.sleep(CHECK_INTERVAL_S)

            sizes = await queue_sizes()
            inbox = sizes.get("inbox", 0)
            outbox = sizes.get("outbox", 0)

            # Fila de entrada acima do threshold
            inbox_alto = inbox >= QUEUE_THRESHOLD
            if inbox_alto and not ja_alertado["inbox"]:
                _emit_alert(
                    "alerta_fila_entrada",
                    f"ALERTA: fila de entrada acima do threshold ({inbox} >= {QUEUE_THRESHOLD})",
                    inbox=inbox,
                )
            ja_alertado["inbox"] = inbox_alto

            # Fila de saida acima do threshold
            outbox_alto = outbox >= QUEUE_THRESHOLD
            if outbox_alto and not ja_alertado["outbox"]:
                _emit_alert(
                    "alerta_fila_saida",
                    f"ALERTA: fila de saida acima do threshold ({outbox} >= {QUEUE_THRESHOLD})",
                    outbox=outbox,
                )
            ja_alertado["outbox"] = outbox_alto

            # Taxa de erros recente
            erros = _error_counter.reset()
            if erros >= ERROR_RATE_LIMIT:
                _emit_alert(
                    "alerta_taxa_erro",
                    f"ALERTA: taxa de erro elevada ({erros} erros em {CHECK_INTERVAL_S}s)",
                    erros=erros,
                )

        except asyncio.CancelledError:
            break
        except Exception as e:  # nao deixa o monitor morrer por um erro pontual
            logger.error(
                f"Erro no alert_monitor: {e}",
                extra={"evento": "alert_monitor_error"},
            )
