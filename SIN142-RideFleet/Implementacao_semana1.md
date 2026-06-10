# RideFleet — Documentação de Implementação Inicial
**SIN 142 — Sistemas Distribuídos — UFV 2026/1**  
**Semana 1 | 08/05 a 14/05/2026**

---

## Visão Geral

O RideFleet é um serviço de transporte por aplicativo implementado com **Python + FastAPI**. Esta documentação descreve as decisões de arquitetura, estrutura do projeto e componentes implementados na Semana 1.

---

## Stack Tecnológica

| Componente | Tecnologia | Justificativa |
|-----------|-----------|---------------|
| Linguagem | Python 3.12 | Legibilidade, curva de aprendizado suave, boa para lógica de SD |
| Framework Web | FastAPI | Alto desempenho, tipagem com Pydantic, docs automáticas (Swagger) |
| Servidor | Uvicorn | Servidor ASGI assíncrono, incluído no `fastapi[standard]` |
| Banco (Sem. 1) | Dicionário em memória | Simplicidade para protótipo inicial; substituído por PostgreSQL na Sem. 2 |
| Testes | pytest + httpx | Testes unitários da lógica de negócio |
| Containerização | Docker + Docker Compose | Integração com o Core do projeto |

---

## Estrutura do Projeto

```
ridefleet/
├── app/
│   ├── main.py                  # Entrypoint FastAPI — registra rotas e health check
│   ├── models/
│   │   ├── ride.py              # Máquina de estados da corrida + modelos Pydantic
│   │   ├── driver.py            # Modelo de motorista
│   │   └── passenger.py        # Modelo de passageiro
│   ├── routes/
│   │   ├── rides.py             # Endpoints HTTP de corrida
│   │   ├── drivers.py           # Endpoints HTTP de motorista
│   │   └── passengers.py       # Endpoints HTTP de passageiro
│   ├── services/
│   │   └── ride_service.py     # Lógica de negócio e política de overflow
│   └── db/
│       └── database.py         # Banco em memória (dicts indexados por id)
├── tests/
│   └── test_rides.py           # Testes unitários — máquina de estados e serviços
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
└── README.md
```

---

## Máquina de Estados da Corrida

A corrida é o objeto central do sistema. Seu ciclo de vida é controlado por uma máquina de estados explícita implementada em `app/models/ride.py`.

### Diagrama de Estados

```
              ┌─────────────────────────────────────────┐
              ↓           ↓           ↓           ↓     │
[REQUEST] → [MATCH] → [CONFIRM] → [IN_TRANSIT] → [COMPLETE]
              │           │           │           │
              └───────────┴───────────┴───────────┘
                              ↓
                         [CANCELLED]
```

### Transições Válidas

| Estado Atual | Pode ir para |
|-------------|-------------|
| `request` | `match`, `cancelled` |
| `match` | `confirm`, `cancelled` |
| `confirm` | `in_transit`, `cancelled` |
| `in_transit` | `complete`, `cancelled` |
| `complete` | — (estado final) |
| `cancelled` | — (estado final) |

### Implementação

A validação de transição é feita pelo método `can_transition_to()` no modelo `Ride`. Qualquer tentativa de transição inválida (ex: `request → complete`) lança uma `ValueError` com mensagem descritiva.

```python
# Exemplo de uso da máquina de estados
ride = Ride(passenger_id="p1", origin="Rua A", destination="Rua B")
ride.transition(RideStatus.MATCH, driver_id="d1")   # OK
ride.transition(RideStatus.COMPLETE)                 # ValueError — pula etapas
```

---

## Modelos de Dados

### Corrida (`Ride`)

| Campo | Tipo | Descrição |
|-------|------|-----------|
| `id` | UUID | Identificador único gerado automaticamente |
| `passenger_id` | str | Referência ao passageiro |
| `origin` | str | Endereço de origem |
| `destination` | str | Endereço de destino |
| `status` | RideStatus | Estado atual na máquina de estados |
| `driver_id` | str? | Motorista atribuído (após MATCH) |
| `delegated_to` | str? | ID do serviço externo (se delegada para fora) |
| `delegated_from` | str? | ID do serviço de origem (se recebida de outro grupo) |
| `created_at` | datetime | Timestamp de criação |
| `updated_at` | datetime | Timestamp da última transição |

### Motorista (`Driver`)

| Campo | Tipo | Descrição |
|-------|------|-----------|
| `id` | UUID | Identificador único |
| `name` | str | Nome do motorista |
| `license_plate` | str | Placa do veículo |
| `phone` | str | Telefone de contato |
| `status` | DriverStatus | `available`, `busy` ou `offline` |
| `current_ride_id` | str? | Corrida atual (se `busy`) |

### Passageiro (`Passenger`)

| Campo | Tipo | Descrição |
|-------|------|-----------|
| `id` | UUID | Identificador único |
| `name` | str | Nome do passageiro |
| `phone` | str | Telefone de contato |

---

## Endpoints da API

A documentação interativa completa está disponível em `http://localhost:8000/docs`.

### Corridas

| Método | Rota | Descrição |
|--------|------|-----------|
| `POST` | `/rides/` | Criar nova corrida (estado inicial: `request`) |
| `GET` | `/rides/` | Listar todas as corridas |
| `GET` | `/rides/{id}` | Detalhes de uma corrida |
| `GET` | `/rides/pending` | Corridas aguardando atribuição de motorista |
| `PATCH` | `/rides/{id}/status` | Aplicar transição de estado |
| `GET` | `/rides/overflow/check` | Verificar se o serviço deve delegar corridas |

### Motoristas

| Método | Rota | Descrição |
|--------|------|-----------|
| `POST` | `/drivers/` | Cadastrar motorista |
| `GET` | `/drivers/` | Listar todos os motoristas |
| `GET` | `/drivers/available` | Listar apenas motoristas disponíveis |
| `GET` | `/drivers/{id}` | Detalhes de um motorista |
| `PATCH` | `/drivers/{id}` | Atualizar dados do motorista |
| `DELETE` | `/drivers/{id}` | Remover motorista |

### Passageiros

| Método | Rota | Descrição |
|--------|------|-----------|
| `POST` | `/passengers/` | Cadastrar passageiro |
| `GET` | `/passengers/` | Listar todos os passageiros |
| `GET` | `/passengers/{id}` | Detalhes de um passageiro |
| `PATCH` | `/passengers/{id}` | Atualizar dados do passageiro |
| `DELETE` | `/passengers/{id}` | Remover passageiro |

### Sistema

| Método | Rota | Descrição |
|--------|------|-----------|
| `GET` | `/health` | Health check básico do serviço |
| `GET` | `/` | Status online do serviço |

---

## Política de Overflow

Implementada em `app/services/ride_service.py`, a função `should_delegate()` decide quando o serviço está congestionado e deve delegar corridas via Core.

**Critério atual (Semana 1):** o serviço delega quando há menos de 1 motorista disponível.

```python
MIN_AVAILABLE_DRIVERS = 1

def should_delegate() -> bool:
    available = len(get_available_drivers())
    return available < MIN_AVAILABLE_DRIVERS
```

Esta política será expandida nas semanas seguintes para considerar também tamanho da fila e latência média.

---

## Lógica de Atribuição de Motorista

Quando uma corrida transiciona para `MATCH` com um `driver_id`:

1. O motorista é marcado como `BUSY`
2. O campo `current_ride_id` do motorista é atualizado

Quando a corrida chega em `COMPLETE` ou `CANCELLED`:

1. O motorista é marcado de volta como `AVAILABLE`
2. O campo `current_ride_id` é limpo

---

## Testes Unitários

Os testes estão em `tests/test_rides.py` e cobrem:

| Teste | O que verifica |
|-------|---------------|
| `test_corrida_criada_com_status_request` | Estado inicial correto |
| `test_transicao_valida_request_para_match` | Transição válida funciona |
| `test_transicao_invalida_levanta_excecao` | Pulos de estado são bloqueados |
| `test_fluxo_completo_happy_path` | Fluxo completo sem erros |
| `test_cancelamento_em_qualquer_estado` | Cancelamento sempre permitido |
| `test_criar_corrida_via_servico` | Integração com serviço e banco |
| `test_overflow_sem_motoristas` | Política de overflow sem motoristas |
| `test_sem_overflow_com_motorista_disponivel` | Política com motorista disponível |
| `test_motorista_fica_busy_apos_match` | Status do motorista após MATCH |
| `test_motorista_fica_disponivel_apos_complete` | Status do motorista após COMPLETE |

Para rodar:

```bash
pytest tests/ -v
```

---

## Como Executar

### Desenvolvimento local

```bash
# 1. Criar e ativar ambiente virtual
python -m venv .venv
.venv\Scripts\activate.bat        # Windows
source .venv/bin/activate          # Linux/Mac

# 2. Instalar dependências
pip install -r requirements.txt

# 3. Rodar o servidor em modo desenvolvimento (com hot reload)
fastapi dev app/main.py

# 4. Acessar a documentação interativa
# http://localhost:8000/docs
```

### Com Docker

```bash
docker compose up --build
```

---

## Decisões de Implementação

**Por que banco em memória na Semana 1?**
O foco desta semana é validar a lógica de negócio (máquina de estados, CRUDs, política de overflow) sem a complexidade de configurar banco de dados. O `database.py` centraliza todos os dados em dicionários, tornando a substituição por PostgreSQL na Semana 2 uma troca cirúrgica sem alterar as camadas de serviço e rotas.

**Por que separar `models`, `routes` e `services`?**
A separação em camadas permite que membros diferentes do grupo trabalhem em paralelo sem conflito. As rotas cuidam apenas de HTTP; os serviços contêm a lógica de negócio; os modelos definem a estrutura dos dados. Isso também facilita os testes unitários, que testam os serviços diretamente sem subir o servidor HTTP.

**Por que Pydantic para os modelos?**
O FastAPI usa Pydantic nativamente para validação e serialização. Todos os dados recebidos via HTTP são validados automaticamente antes de chegar no código da aplicação, eliminando uma classe inteira de bugs de entrada.

---

## Próximos Passos — Semana 2

- Substituir banco em memória por **PostgreSQL** com persistência real
- Implementar **logging estruturado** (JSON) com campos: `timestamp`, `evento`, `corrida_id`, `estado_anterior`, `estado_novo`
- Implementar **fila de corridas** (entrada e saída) com RabbitMQ ou Redis Streams
- Configurar **load balancer** (Nginx) com 2 instâncias do serviço
- Expandir o endpoint `/health` com tamanho da fila e latência média
- Configurar pipeline **CI/CD** com GitHub Actions

---

*Documentação gerada em 09/05/2026 — SIN 142 Sistemas Distribuídos UFV*
