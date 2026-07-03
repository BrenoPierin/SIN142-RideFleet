# CONTRIBUTIONS

## Trabalho Desenvolvido por:

- Breno Silva Pierin - 8154
- Luan Siqueira Ramos - 8131
- Lucas Brasil Oliveira - 8755
- Luana Amie Shimomaebara Nishida - 8111

---

## O que foi implementado

O RideFleet é um serviço de transporte que consome os mecanismos de sistemas distribuídos do Core e implementa a própria infraestrutura de serviço. Ao longo do período, foram desenvolvidos:

**Back-end do serviço.** API em FastAPI com a máquina de estados da corrida (`request → match → confirm → in_transit → complete`), gestão de motoristas e passageiros, e persistência em PostgreSQL com SQLAlchemy async.

**Integração com o Core.** Registro do serviço com retry/backoff, consumo dos endpoints do contrato, participação nos leilões de delegação e tratamento dos callbacks de proposta e atribuição.

**Filas de corridas.** Filas de entrada e saída com Redis Streams, usando consumer groups, confirmação por ACK e reprocessamento de mensagens pendentes via `XAUTOCLAIM`. A fila persiste entre reinícios.

**Relógio lógico de Lamport.** Contador compartilhado entre as instâncias no Redis, atualizado de forma atômica por script Lua, para correlacionar causalmente os eventos com o log do Core.

**Load balancer.** Nginx em round-robin na frente de duas instâncias do serviço, expostas ao Core apenas através do balanceador.

**Lógica de delegação.** Política de overflow para delegar corridas quando faltam motoristas (delegação de saída) e processamento de corridas recebidas de outros grupos (delegação de entrada), sempre passando pelo Core.

**Observabilidade.** Endpoint de métricas no padrão Prometheus (corridas locais, delegadas e recebidas, latência, throughput, estado do serviço e tamanho das filas), com dashboards no Grafana e carga visível por instância.

**Logging estruturado e health check.** Logs em formato estruturado e endpoint de saúde classificando o serviço em `UP` / `DEGRADED` / `DOWN`, integrado ao healthcheck do Docker.

**Resiliência.** Tratamento do fallback do circuit breaker do Core, degradação graciosa sob carga e compensação da saga quando a delegação falha.

**Front-end.** Interface em React/Vite para solicitação de corrida, acompanhamento do status em tempo real e indicação de qual grupo atendeu quando há delegação.

**CI/CD e containerização.** Pipeline automatizado com testes e orquestração de todo o ambiente via Docker Compose.

---

> Projeto desenvolvido para a disciplina **SIN 142 — Sistemas Distribuídos** — Universidade Federal de Viçosa (UFV) — 2026/1.
