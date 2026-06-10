# RideFleet — Documentação da Semana 3
**SIN 142 — Sistemas Distribuídos — UFV 2026/1**
**Período: 22/05 a 28/05/2026**

---

## Visão Geral

A Semana 3 conectou o serviço RideFleet ao **Core** e implementou o fluxo completo de **delegação** entre grupos, sempre intermediado pelo Core (nunca serviço-a-serviço). Foram integrados os mecanismos de sistemas distribuídos: relógios lógicos de Lamport, travas distribuídas, leilão (consenso) e saga de transições de estado com compensação. Adicionalmente, foram concluídos itens de infraestrutura: pipeline de CI, healthcheck das instâncias no Compose e alertas básicos.

---

## Estrutura do Projeto (adições da Semana 3)

```
SIN142-RideFleet/
├── .github/
│   └── workflows/
│       └── ci.yml                    # NOVO — pipeline de testes (GitHub Actions)
├── app/
│   ├── main.py                       # lifespan: auto-registro + 3 workers de background
│   ├── core/
│   │   ├── core_client.py            # cliente HTTP do Core (todos os endpoints do contrato)
│   │   ├── lamport.py                # relógio lógico de Lamport (persistido no Redis)
│   │   ├── logging.py                # logging estruturado (campos do Core)
│   │   └── queue.py                  # filas inbox/outbox (Redis Streams)
│   ├── routes/
│   │   ├── core_callbacks.py         # callbacks: /rides/incoming e /rides/{uuid}/assigned
│   │   └── audit.py                  # consulta ao log causal e ao status no Core
│   └── services/
│       ├── delegation_service.py     # delegação de SAÍDA (outbox -> Core)
│       ├── inbox_worker.py           # delegação de ENTRADA (inbox -> motorista -> saga)
│       └── monitor.py                # NOVO — alertas básicos em background
├── nginx/nginx.conf
├── docker-compose.yml                # healthcheck nas instâncias do app (NOVO)
├── tests/
│   ├── test_ride_state_machine.py
│   └── test_semana3.py               # callbacks do Core + Lamport
└── requirements.txt
```

---

## O que foi implementado

### ✅ Cliente da API do Core

**Arquivo:** `app/core/core_client.py`

Implementa o consumo de todos os endpoints do contrato do Core via HTTP (`httpx` assíncrono):

| Função | Endpoint do Core | Finalidade |
|--------|------------------|-----------|
| `register_group()` | `POST /groups/register` | Auto-registro do grupo (idempotente) no startup |
| `create_ride_core()` | `POST /rides` | Delegação de saída: inicia o leilão no Core |
| `get_ride_status()` | `GET /rides/{uuid}/status` | Estado atual da corrida no Core |
| `get_ride_proposals()` | `GET /rides/{uuid}/proposals` | Propostas e vencedor do leilão |
| `get_ride_audit()` | `GET /rides/{uuid}/audit` | Log causal (eventos ordenados por Lamport) |
| `transition_ride_core()` | `PATCH /rides/{uuid}/status` | Transição de estado da saga |
| `acquire_lock()` | `POST /locks/{uuid}` | Adquire trava distribuída antes da saga |
| `release_lock()` | `DELETE /locks/{uuid}` | Libera a trava ao concluir |

O endereço do Core é configurado por `CORE_URL` e a autenticação por `X-API-Key` (obtida no registro).

> **Decisão de rede (Windows/Docker Desktop):** tanto a chamada RideFleet → Core (`CORE_URL`) quanto Core → RideFleet (`SERVICE_URL`) usam `host.docker.internal` com as portas publicadas (8080 e 8000). Essa abordagem é mais confiável no Windows que depender de DNS por rede compartilhada, e mantém o Core enxergando apenas o Nginx (porta 8000).

---

### ✅ Relógios Lógicos de Lamport

**Arquivo:** `app/core/lamport.py`

Relógio de Lamport persistido no Redis, compartilhado entre as duas instâncias:

| Função | Regra |
|--------|-------|
| `tick()` | Incrementa e retorna o relógio local (evento interno) |
| `update(received)` | Aplica `local = max(local, received) + 1` ao receber mensagem |
| `current()` | Lê o valor atual sem incrementar |

Cada evento de corrida carrega o `logicalTimestamp`, permitindo correlacionar os logs do RideFleet com o log causal do Core e ordenar causalmente os eventos do leilão e da saga.

---

### ✅ Delegação de SAÍDA (overflow)

**Arquivos:** `app/services/ride_service.py`, `app/services/delegation_service.py`

Quando a política de overflow é atingida (`available_drivers < MIN_AVAILABLE_DRIVERS`), a corrida é enfileirada na **outbox** em vez de atendida localmente. O `delegation_worker` (background) consome a outbox e chama `create_ride_core()`, iniciando o leilão no Core.

Eventos de log: `corrida_enfileirada_saida` (WARN) → `corrida_enviada_core` → `corrida_delegada_core`.

---

### ✅ Delegação de ENTRADA (leilão + saga)

**Arquivos:** `app/routes/core_callbacks.py`, `app/services/inbox_worker.py`

Fluxo completo quando o Core delega uma corrida para o RideFleet:

1. **`POST /rides/incoming`** — o Core convida o grupo para o leilão. O RideFleet avalia capacidade e responde com proposta (`estimatedEta`, `estimatedPrice`, `logicalTimestamp`) ou `204` se sem motoristas. Eventos: `leilao_recebido`, `leilao_proposta_enviada`.
2. **`POST /rides/{uuid}/assigned`** — o Core informa que o grupo venceu. A corrida é enfileirada na **inbox**. Eventos: `leilao_ganho`, `corrida_enfileirada_entrada`.
3. **`inbox_worker`** (background) processa a corrida: salva localmente (idempotente), atribui motorista, adquire a **trava distribuída** no Core e executa a **saga** de transições `confirm → in_transit → complete`, liberando a trava ao final. Eventos: `corrida_delegada_recebida`, `lock_adquirido`, `transicao_saga` (×3), `corrida_delegada_concluida`.

**Travas distribuídas (Req. 1):** antes de transicionar, o grupo adquire o lock da corrida no Core (`acquire_lock`, TTL 60s). Só o detentor pode avançar a saga; ao concluir, o lock é liberado.

**Saga / commit distribuído (Req. 2):** as transições são enviadas em sequência ao Core. Se o grupo não concluir dentro do TTL do lock, o `lock_monitor` do Core expira a trava e **compensa** a corrida (re-leilão), garantindo que ela não fique presa.

**Idempotência:** `_save_delegated_ride` faz upsert (busca por UUID e atualiza se já existir), evitando violação de PK em reprocessamento da fila.

---

### ✅ Auditoria / Log Causal

**Arquivo:** `app/routes/audit.py`

Endpoints para inspecionar o estado distribuído a partir do RideFleet:

| Método | Rota | Descrição |
|--------|------|-----------|
| `GET` | `/audit/rides/{uuid}` | Log causal da corrida (eventos do Core ordenados por Lamport) |
| `GET` | `/audit/rides/{uuid}/status` | Status atual da corrida no Core |
| `GET` | `/audit/rides/{uuid}/proposals` | Propostas e vencedor do leilão |
| `GET` | `/audit/clock` | Valor atual do relógio de Lamport local |

---

### ✅ Alertas Básicos (conclusão da Semana 2)

**Arquivo:** `app/services/monitor.py`

Worker em background que verifica periodicamente (a cada 15s) e emite log estruturado de **ALERTA** (nível WARN, campo `"alerta": true`) quando:

- a fila de entrada ou de saída ultrapassa `QUEUE_OVERFLOW_THRESHOLD` (default 10);
- a taxa de erros recente passa de `ALERT_ERROR_RATE` (default 5 por intervalo).

Os alertas são desduplicados por mudança de estado (só logam na transição normal → alerta), evitando flood. Não há dependência de stack externa — os alertas usam o mesmo log JSON consultável.

---

### ✅ Pipeline de CI

**Arquivo:** `.github/workflows/ci.yml`

GitHub Actions que, a cada push e pull request, configura Python 3.12, instala as dependências e executa `pytest`. Os testes usam SQLite em memória e mocks, rodando sem necessidade de PostgreSQL, Redis ou Core.

---

### ✅ Healthcheck das instâncias no Compose

**Arquivo:** `docker-compose.yml`

As instâncias `ridefleet-1` e `ridefleet-2` passaram a ter `healthcheck` apontando para `/health` (via `httpx`, já presente na imagem). Combinado com `restart: unless-stopped`, o estado de saúde das instâncias fica visível no `docker ps` e integrado ao Compose.

---

## Containerização e Integração

- O RideFleet sobe junto com o Core compartilhando a stack: PostgreSQL e Redis próprios, Nginx como load balancer, duas instâncias do serviço.
- Toda mensagem de delegação passa pelo Core — não há comunicação direta entre serviços parceiros.
- O Core enxerga apenas o Nginx (`http://host.docker.internal:8000`), nunca as instâncias diretamente.

---

## Endpoints da Semana 3

| Método | Rota | Descrição |
|--------|------|-----------|
| `POST` | `/rides/incoming` | Recebe convite de leilão do Core; responde proposta |
| `POST` | `/rides/{uuid}/assigned` | Recebe notificação de vitória; enfileira na inbox |
| `GET` | `/audit/rides/{uuid}` | Log causal da corrida no Core |
| `GET` | `/audit/rides/{uuid}/status` | Status da corrida no Core |
| `GET` | `/audit/rides/{uuid}/proposals` | Propostas e vencedor do leilão |
| `GET` | `/audit/clock` | Relógio de Lamport atual |

---

## Como Executar

```bash
# 1. Sobe o Core (em seu diretório)
cd ../ridefleet-core-sin142
docker compose -f infra/docker-compose.core.yml up -d --build

# 2. Sobe o RideFleet
cd ../SIN142-RideFleet
docker compose up -d --build
```

Validação dos três fluxos (Windows/PowerShell):

```powershell
.\simula_docker_real.ps1 -SomenteFluxoA   # corrida local
.\simula_docker_real.ps1 -SomenteFluxoB   # delegação de saída (overflow)
.\simula_docker_real.ps1 -SomenteFluxoC   # delegação de entrada (leilão)
```

---

## Testes

```bash
pip install -r requirements.txt
pytest -v
```

**Cobertura:**

| Arquivo | Escopo |
|---------|--------|
| `test_ride_state_machine.py` | Máquina de estados, serviço de corridas, overflow, fila Redis, logging |
| `test_semana3.py` | Callbacks do Core (`/rides/incoming`, `/rides/{uuid}/assigned`), relógio de Lamport, endpoints de auditoria |

Execução atual: **66 testes passando**.

---

## Correções aplicadas na integração real (Core + RideFleet)

Durante a validação em Docker real, foram corrigidos os seguintes problemas (detalhados em `PATCHES_SIN142.md`):

| # | Componente | Problema | Correção |
|---|-----------|----------|----------|
| 1 | Core | datetime aware/naive em `ride_proposals` | `_utcnow()` naive UTC |
| 2 | RideFleet | e-mail `.local` rejeitado (422) | domínio `.example.com` |
| 3 | RideFleet | DNS `core` inalcançável | `CORE_URL` via `host.docker.internal:8080` |
| 4 | Core | trava de Clock jump no Lamport | remover `raise`, manter `max+1` |
| 5 | RideFleet | PK duplicada na inbox | `_save_delegated_ride` idempotente (upsert) |
| 6 | Core | grupo fantasma no leilão | alinhar `SERVICE_NAME` + reset de grupos |
| 7 | Core | `json_extract` (SQLite) em PostgreSQL | `payload[...].as_integer()` (agnóstico) |
| 8 | RideFleet | `httpx delete(json=)` no release_lock | `client.request("DELETE", ...)` |

---

## Checklist da Semana 3

- [x] Cliente HTTP consumindo todos os endpoints do contrato do Core
- [x] Solicitação de delegação (broadcast de leilão) via Core
- [x] Recebimento de proposta (ETA + preço)
- [x] Confirmação de aceite (`/rides/{uuid}/assigned`)
- [x] Notificação de status de corrida (saga via PATCH)
- [x] Registro de eventos no log causal (Lamport)
- [x] Delegação de saída (overflow → outbox → Core)
- [x] Delegação de entrada (inbox → motorista → saga)
- [x] Travas distribuídas (acquire/release no Core) — Req. 1
- [x] Saga / commit distribuído com compensação — Req. 2
- [x] Leilão / consenso (proposta + seleção de vencedor) — Req. 3
- [x] Relógios de Lamport integrados ao logging — Req. 5
- [x] Containerização (sobe com `docker compose up`)
- [x] Todas as delegações passam pelo Core (nunca direto)
- [x] Pipeline de CI executando os testes
- [x] Healthcheck das instâncias integrado ao Compose
- [x] Alertas básicos (fila e taxa de erro)
- [ ] Suíte de testes de contrato oficial do Core (executar se fornecida)

> **Nota — Req. 4 (Circuit Breaker):** o circuit breaker é mantido no **Core** (estados CLOSED/OPEN/HALF_OPEN, visíveis no dashboard Grafana do Core). O RideFleet é o serviço monitorado por esse breaker.

---

*SIN 142 — Sistemas Distribuídos — Universidade Federal de Viçosa — 2026/1*
*Professores: Rodrigo Moreira, Ph.D. e Pedro Damaso, Ph.D.*
