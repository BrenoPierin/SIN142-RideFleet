# RideFleet — Documentação da Semana 2
**SIN 142 — Sistemas Distribuídos — UFV 2026/1**
**Período: 15/05 a 21/05/2026**

---

## Visão Geral

A Semana 2 teve como foco a implementação dos mecanismos de infraestrutura distribuída próprios do serviço cliente. Adicionalmente, foi concluída a pendência da Semana 1 referente à substituição do banco de dados em memória por um banco relacional persistente.

---

## Estrutura do Projeto

```
SIN142-RideFleet/
├── app/
│   ├── main.py                  # Entrypoint FastAPI com lifespan
│   ├── core/
│   │   ├── logging.py           # Logging estruturado em JSON
│   │   └── queue.py             # Fila com Redis Streams
│   ├── db/
│   │   ├── database.py          # Conexão assíncrona PostgreSQL (SQLAlchemy)
│   │   └── orm_models.py        # Tabelas: rides, drivers, passengers
│   ├── models/
│   │   ├── ride.py              # Máquina de estados + schemas Pydantic
│   │   ├── driver.py            # Schema de motorista
│   │   └── passenger.py        # Schema de passageiro
│   ├── queue/
│   │   └── redis_queue.py       # Módulo auxiliar de fila Redis
│   ├── routes/
│   │   ├── rides.py             # Endpoints de corrida
│   │   ├── drivers.py           # Endpoints de motorista
│   │   ├── passengers.py       # Endpoints de passageiro
│   │   └── health.py            # Health check expandido
│   ├── services/
│   │   └── ride_service.py     # Lógica de negócio + política de overflow
│   └── worker.py               # Worker de processamento da fila de entrada
├── nginx/
│   └── nginx.conf              # Load balancer round-robin
├── tests/
│   └── test_ride_state_machine.py
├── docker-compose.yml
├── Dockerfile
├── pytest.ini
└── requirements.txt
```

---

## O que foi implementado

### ✅ Pendência da Semana 1 — Banco de Dados PostgreSQL

O banco em memória (`dict`) foi substituído por **PostgreSQL 16** com SQLAlchemy assíncrono.

**Arquivos:** `app/db/database.py`, `app/db/orm_models.py`

**Tabelas criadas:**

| Tabela | Campos principais |
|--------|------------------|
| `rides` | id, passenger_id, origin, destination, status, driver_id, delegated_to, delegated_from, created_at, updated_at |
| `drivers` | id, name, license_plate, phone, status, current_ride_id, created_at |
| `passengers` | id, name, phone, created_at |

**Decisão técnica:** modelos SQLAlchemy (ORM) separados dos schemas Pydantic (validação HTTP). Isso permite que as rotas continuem trabalhando com objetos tipados enquanto a camada de persistência usa os modelos ORM. As tabelas são criadas automaticamente no startup via `create_tables()`.

---

### ✅ Logging Estruturado em JSON

**Arquivo:** `app/core/logging.py`

Todo evento significativo do serviço gera uma linha JSON com os campos obrigatórios definidos pelo Core:

```json
{
  "timestamp": "2026-05-15T14:32:01.123456+00:00",
  "level": "INFO",
  "service": "ridefleet-1",
  "message": "[corrida_criada] corrida=uuid-abc",
  "evento": "corrida_criada",
  "corrida_id": "uuid-abc",
  "servico_origem": "ridefleet-1",
  "estado_anterior": null,
  "estado_novo": "request"
}
```

**Níveis de log:**

| Nível | Quando usar |
|-------|------------|
| `INFO` | Fluxo normal: corrida criada, transição de estado, motorista atribuído |
| `WARN` | Degradação: corrida enfileirada por overflow, corrida cancelada |
| `ERROR` | Falha: banco inacessível, Redis inacessível, exceção não tratada |

**Função principal:** `log_ride_event()` centraliza todos os logs de corrida com os campos obrigatórios do Core, garantindo correlação futura com os timestamps lógicos de Lamport (Semana 3).

---

### ✅ Fila de Corridas — Redis Streams

**Arquivos:** `app/core/queue.py`, `app/queue/redis_queue.py`, `app/worker.py`

Implementada com **Redis Streams** (`XADD`, `XREADGROUP`, `XACK`, `XAUTOCLAIM`), oferecendo garantias superiores ao Redis Lists:

**Streams:**

| Stream | Chave Redis | Finalidade |
|--------|-------------|-----------|
| Inbox | `ridefleet:stream:inbox` | Corridas delegadas recebidas de outros grupos aguardando motorista local |
| Outbox | `ridefleet:stream:outbox` | Corridas em overflow aguardando delegação via Core |

**Garantias implementadas:**

- **ACK explícito** (`XACK`) — mensagem permanece pendente até confirmação de processamento
- **Consumer group** (`ridefleet-consumers`) — múltiplas instâncias consomem sem duplicação
- **Reprocessamento** (`XAUTOCLAIM`) — mensagens travadas por mais de 60s são reivindicadas por outra instância
- **Persistência AOF** — Redis grava cada operação em disco antes de confirmar
- **Tamanho máximo** de 10.000 mensagens por stream (`MAXLEN`)

**Worker:** `app/worker.py` processa a fila de entrada em background, atribuindo corridas recebidas por delegação a motoristas locais disponíveis.

#### Por que Redis Streams e não RabbitMQ ou Kafka?

O Redis já é necessário para os locks distribuídos do Requisito 1 (Semana 3). Os Streams oferecem consumer groups nativos com complexidade operacional muito menor que Kafka. Para o volume esperado do projeto, a escolha é a mais simples com as garantias necessárias.

---

### ✅ Monitoramento e Health Check

**Arquivo:** `app/routes/health.py`

O endpoint `/health` realiza verificação ativa do banco e do Redis, retornando diagnóstico completo:

```json
{
  "status": "UP",
  "available_drivers": 3,
  "queue": {
    "inbox": 0,
    "outbox": 0
  },
  "latency_ms": 12.4,
  "issues": []
}
```

**Estados:**

| Status | Condição |
|--------|---------|
| `UP` | Banco e Redis acessíveis, motoristas disponíveis, filas dentro do threshold |
| `DEGRADED` | Nenhum motorista disponível ou fila acima de 10 corridas |
| `DOWN` | Banco ou Redis inacessíveis |

**Integração com Docker Compose:** `restart: unless-stopped` + healthchecks nas dependências (PostgreSQL via `pg_isready`, Redis via `redis-cli ping`) garantem restart automático em caso de falha.

---

### ✅ Load Balancer — Nginx

**Arquivo:** `nginx/nginx.conf`

Nginx configurado como load balancer **round-robin** na frente de duas instâncias do serviço.

**Topologia:**

```
[Core / Externo]
      │
      ▼
  [Nginx :8000]          ← único ponto de entrada visível
   /         \
[ridefleet-1] [ridefleet-2]   ← instâncias internas
      │               │
      └───────┬────────┘
              ▼
        [PostgreSQL]    ← banco compartilhado
        [Redis]         ← fila compartilhada
```

O Core enxerga apenas o Nginx na porta 8000 — nunca as instâncias diretamente, conforme exigido no cronograma.

---

## Stack Tecnológica

| Componente | Tecnologia | Observação |
|-----------|-----------|-----------|
| Linguagem | Python 3.12 | — |
| Framework Web | FastAPI + Uvicorn | 0.115+ |
| Banco de dados | PostgreSQL 16 | Imagem Alpine |
| ORM | SQLAlchemy 2.0+ | Assíncrono com asyncpg |
| Fila de mensagens | Redis 7 — Streams | Imagem Alpine, AOF ativado |
| Load Balancer | Nginx | Alpine, round-robin |
| Containerização | Docker + Docker Compose | Todas as dependências |
| Testes | pytest + aiosqlite | SQLite em memória nos testes |

---

## Endpoints Disponíveis

| Método | Rota | Descrição |
|--------|------|-----------|
| `GET` | `/` | Status online do serviço |
| `GET` | `/health` | Health check completo |
| `POST` | `/rides/` | Criar corrida |
| `GET` | `/rides/` | Listar corridas |
| `GET` | `/rides/pending` | Corridas aguardando motorista |
| `PATCH` | `/rides/{id}/status` | Transicionar estado da corrida |
| `GET` | `/rides/overflow/check` | Verificar se deve delegar |
| `POST` | `/drivers/` | Cadastrar motorista |
| `GET` | `/drivers/` | Listar motoristas |
| `GET` | `/drivers/available` | Motoristas disponíveis |
| `PATCH` | `/drivers/{id}` | Atualizar motorista |
| `DELETE` | `/drivers/{id}` | Remover motorista |
| `POST` | `/passengers/` | Cadastrar passageiro |
| `GET` | `/passengers/` | Listar passageiros |
| `PATCH` | `/passengers/{id}` | Atualizar passageiro |
| `DELETE` | `/passengers/{id}` | Remover passageiro |

Documentação interativa: `http://localhost:8000/docs`

---

## Como Executar

```bash
# Sobe tudo: PostgreSQL + Redis + Nginx + 2 instâncias do serviço
docker compose up --build

# Parar tudo
docker compose down

# Parar e apagar dados (banco + filas)
docker compose down -v
```

---

## Testes

Os testes usam **SQLite em memória** e **Redis mockado** — sem necessidade de PostgreSQL ou Redis rodando localmente.

```bash
# Instalar dependências de teste
pip install aiosqlite pytest pytest-asyncio httpx

# Rodar testes
pytest tests/ -v
```

**Cobertura atual:**

| Arquivo | Escopo |
|---------|--------|
| `test_ride_state_machine.py` | Máquina de estados, serviço de corridas, política de overflow, fila Redis |

---

## Checklist da Semana 2

- [x] Banco de dados PostgreSQL substituindo banco em memória
- [x] Logging estruturado JSON com campos obrigatórios do Core
- [x] Níveis de log: INFO, WARN, ERROR
- [x] Logs correlacionáveis com timestamps lógicos do Core
- [x] Endpoint `/health` com status UP / DEGRADED / DOWN
- [x] Health check expõe motoristas disponíveis e tamanho das filas
- [x] Status DEGRADED como alerta automático
- [x] Health check integrado ao Docker Compose com restart automático
- [x] Fila de entrada (Redis Stream inbox)
- [x] Fila de saída (Redis Stream outbox)
- [x] Persistência das filas via Redis AOF
- [x] ACK explícito e reprocessamento via XAUTOCLAIM
- [x] Consumer group compartilhado entre instâncias
- [x] Worker de processamento da fila de entrada
- [x] Duas instâncias do serviço
- [x] Nginx como load balancer round-robin
- [x] Core enxerga apenas o Nginx

---

## Próximos Passos — Semana 3

- [ ] Consumir a API do Core (delegação, proposta, aceite, auditoria)
- [ ] **Req. 1** — Travas Distribuídas: Redlock sobre o Redis já instalado
- [ ] **Req. 2** — Commit Distribuído / Saga: compensação em caso de falha na delegação
- [ ] **Req. 3** — Consenso / Leilão: broadcast para grupos parceiros, seleção determinística
- [ ] **Req. 4** — Circuit Breaker: estados CLOSED / OPEN / HALF-OPEN
- [ ] **Req. 5** — Relógios Lógicos de Lamport integrados ao logging estruturado
- [ ] Testes de contrato do Core executando no pipeline CI

---

*SIN 142 — Sistemas Distribuídos — Universidade Federal de Viçosa — 2026/1*
*Professores: Rodrigo Moreira, Ph.D. e Pedro Damaso, Ph.D.*
