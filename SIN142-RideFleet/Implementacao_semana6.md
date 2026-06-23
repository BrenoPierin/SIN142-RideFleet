# RideFleet — Documentação da Fase 4
**SIN 142 — Sistemas Distribuídos — UFV 2026/1**
**Entregas: Observabilidade (3,0) e CI/CD (2,0)**
**Período: 05/06 a 11/06/2026**

---

## Visão Geral

A Fase 4 tornou o RideFleet **observável** e **entregável de forma automatizada**. Do lado da observabilidade, o serviço passou a expor um endpoint de métricas no padrão Prometheus (`/metrics`), cobrindo corridas locais vs. delegadas, corridas recebidas por delegação, latência e throughput dos endpoints, estado do serviço, tamanho das filas e distribuição de carga entre instâncias — métricas prontas para serem raspadas pelo Prometheus e exibidas no dashboard Grafana do Core. Do lado de CI/CD, o pipeline foi expandido para o fluxo completo **build → testes unitários → testes de integração → testes de contrato do Core → publicação da imagem → deploy automático na branch principal**.

Nenhuma das mudanças altera o comportamento funcional já validado nas semanas anteriores: a instrumentação é aditiva (incrementa contadores nos pontos de decisão já existentes) e a suíte de testes continua **100% verde** (66 testes anteriores + 5 novos de contrato/métricas).

---

## Estrutura do Projeto (adições da Fase 4)

```
SIN142-RideFleet/
├── .github/
│   └── workflows/
│       └── ci.yml                       # REESCRITO — pipeline CI/CD completo
├── app/
│   ├── main.py                          # registra middleware de métricas + rota /metrics
│   ├── core/
│   │   └── metrics.py                   # NOVO — núcleo das métricas Prometheus
│   ├── routes/
│   │   ├── metrics.py                   # NOVO — endpoint GET /metrics
│   │   └── core_callbacks.py            # +1 contador (corridas recebidas)
│   └── services/
│       ├── ride_service.py              # +1 contador (corridas locais)
│       └── delegation_service.py        # +1 contador (corridas delegadas para fora)
├── monitoring/
│   └── prometheus-scrape.example.yml    # NOVO — job de scrape para o Prometheus do Core
├── tests/
│   ├── conftest.py                      # NOVO — banco SQLite nos testes
│   └── test_core_contract.py            # NOVO — contrato do Core + verificação das métricas
├── docker-compose.yml                   # INSTANCE_ID e SERVICE_VERSION por instância
├── Dockerfile                           # --workers 1 (exposição Prometheus por processo)
└── requirements.txt                     # + prometheus-client
```

---

## Parte 1 — Observabilidade (3,0 pts)

### ✅ Endpoint de métricas (padrão Prometheus)

**Arquivos:** `app/core/metrics.py`, `app/routes/metrics.py`, `app/main.py`

O serviço expõe `GET /metrics` no formato de exposição do Prometheus (`text/plain; version=0.0.4`), usando a biblioteca oficial `prometheus-client`. As métricas vivem em um *registry* próprio (evita colisão de coletores ao reimportar o módulo nos testes). Um *middleware* mede toda requisição HTTP, e os gauges de ponto-no-tempo (estado, filas, motoristas) são atualizados no momento do scrape.

### ✅ Métricas expostas

| Métrica | Tipo | O que mede |
|---------|------|------------|
| `ridefleet_rides_local_total` | Counter | Corridas atendidas localmente (motorista do próprio grupo) |
| `ridefleet_rides_delegated_out_total` | Counter | Corridas delegadas para fora via Core (overflow) |
| `ridefleet_rides_received_delegation_total` | Counter | Corridas recebidas por delegação de outros grupos (label `origin_service`) |
| `ridefleet_ride_request_latency_seconds` | Histogram | Latência dos endpoints de corrida (buckets de 5 ms a 10 s) |
| `ridefleet_http_requests_total` | Counter | Total de requisições HTTP — base do throughput (req/s via `rate()`) |
| `ridefleet_service_state` | Gauge | Estado do serviço: `0` disponível, `1` congestionado, `2` fora do ar |
| `ridefleet_queue_size` | Gauge | Tamanho atual das filas (label `queue="inbox"`/`"outbox"`) |
| `ridefleet_available_drivers` | Gauge | Motoristas disponíveis no momento |
| `ridefleet_build_info` | Gauge | Metadados da instância (label `version`) |

Todas as séries carregam os labels `service` e `service_instance`. O `service_instance` (nome do container) é o que permite ao Grafana mostrar a **distribuição de carga entre instâncias**.

### ✅ Pontos de instrumentação

| Métrica | Onde é incrementada | Regra |
|---------|---------------------|-------|
| `rides_local_total` | `ride_service.transition_ride` | Transição para `MATCH` com motorista local e corrida **não** delegada de fora |
| `rides_delegated_out_total` | `delegation_service.delegation_worker` | Após sucesso em `create_ride_core()` (envio ao Core) |
| `rides_received_delegation_total` | `core_callbacks.receive_assignment` | Ao vencer o leilão (callback `/rides/{uuid}/assigned`) |
| `http_requests_total` + latência | `PrometheusMiddleware` | A cada requisição; latência só para rotas `/rides` |
| Gauges (estado/filas/motoristas) | `routes/metrics.py` | Recalculados no momento do scrape, mesmo critério do `/health` |

> **Decisão — normalização de endpoints:** o label `endpoint` colapsa UUIDs e números (`/rides/<uuid>/status` → `/rides/{id}/status`). Sem isso, cada corrida geraria uma série nova e explodiria a cardinalidade do Prometheus.

> **Decisão — 1 worker por container:** o `Dockerfile` fixa `--workers 1`. A exposição padrão do `prometheus-client` é por processo; a escala horizontal é feita por **múltiplos containers + load balancer**, não por múltiplos workers no mesmo container — o que também é o que torna a distribuição de carga visível por instância.

> **Decisão — label `service_instance` (e não `instance`):** os nomes `instance` e `job` são reservados e injetados pelo Prometheus no scrape. Usar um label próprio (`service_instance`) evita conflito de relabeling.

### ✅ Integração com o Grafana do Core

**Arquivo:** `monitoring/prometheus-scrape.example.yml`

O Prometheus do Core precisa raspar `/metrics` de **cada instância diretamente** (`ridefleet-grupo-a-1:8000`, `ridefleet-grupo-a-2:8000`) — **nunca** via Nginx, pois o round-robin do load balancer embaralharia as amostras entre as duas instâncias. O arquivo traz o job de scrape pronto e as consultas PromQL dos painéis, por exemplo:

```promql
# Throughput (req/s) por instância
sum by (service_instance) (rate(ridefleet_http_requests_total[1m]))

# Latência p95 dos endpoints de corrida
histogram_quantile(0.95, sum by (le, endpoint)
  (rate(ridefleet_ride_request_latency_seconds_bucket[5m])))

# Locais vs. delegadas para fora
sum(rate(ridefleet_rides_local_total[5m]))
sum(rate(ridefleet_rides_delegated_out_total[5m]))
```

---

## Parte 2 — CI/CD (2,0 pts)

### ✅ Pipeline em etapas

**Arquivo:** `.github/workflows/ci.yml`

O pipeline (GitHub Actions) executa, nesta ordem:

| Job | Disparo | O que faz |
|-----|---------|-----------|
| `build` | todo push/PR | Build da imagem Docker (valida o `Dockerfile`, com cache) |
| `unit-tests` | todo push/PR | `pytest tests/test_ride_state_machine.py` (máquina de estados) |
| `integration-tests` | todo push/PR | `pytest tests/test_semana3.py` com serviço Redis no runner |
| `contract-tests` | todo push/PR | `pytest tests/test_core_contract.py` (contrato do Core + métricas) |
| `publish` | push na `main` | Build & push da imagem no GHCR (`latest` + SHA) |
| `deploy` | push na `main` | SSH no servidor: `docker compose pull && up -d` |

Os jobs `publish` e `deploy` só rodam após todos os testes passarem e somente em push na branch principal (`if: github.ref == 'refs/heads/main'`), atendendo ao requisito de **deploy automático ao fazer push na branch principal**.

### ✅ Testes de contrato do Core

**Arquivo:** `tests/test_core_contract.py`

Rodam **offline** (sem o Core no ar) e validam os dois lados do contrato:

1. **O que o Core chama em nós:** os callbacks obrigatórios `POST /rides/incoming` e `POST /rides/{uuid}/assigned` existem e respondem.
2. **O que nós chamamos no Core:** o `core_client` monta método, caminho, header `X-API-Key` e payload exatamente no formato documentado (registro de grupo, criação de corrida, transição, locks). O `httpx` é substituído por um cliente falso que captura a requisição.
3. **Observabilidade:** o `/metrics` expõe todas as séries obrigatórias.

> Se o Core publicar um OpenAPI ou um mock oficial, estes testes podem passar a validar contra ele; enquanto isso, garantem que o cliente não "derive" do contrato sem que o pipeline acuse.

### ✅ Secrets necessários (deploy)

Configurados em **Settings → Secrets and variables → Actions**:

| Secret | Conteúdo |
|--------|----------|
| `DEPLOY_HOST` | IP ou domínio do servidor |
| `DEPLOY_USER` | usuário SSH |
| `DEPLOY_SSH_KEY` | chave privada SSH (conteúdo completo) |
| `DEPLOY_PATH` | pasta no servidor onde está o `docker-compose.yml` |

A publicação no GHCR usa o `GITHUB_TOKEN` automático — não exige secret adicional. O nome da imagem é convertido para minúsculo no pipeline (exigência do GHCR).

---

## Como validar

```bash
# 1. Dependência nova
pip install -r requirements.txt

# 2. Testes (devem passar 71: 66 anteriores + 5 novos)
pytest -v

# 3. Sistema no ar (rebuild obrigatório: Dockerfile e requirements mudaram)
docker compose up -d --build
docker compose ps

# 4. Métricas de uma instância (direto no container, não no Nginx)
docker compose exec ridefleet-1 curl -s http://localhost:8000/metrics
```

Gerando tráfego pelo front (criar corrida, cadastrar motorista, forçar overflow), os contadores `ridefleet_rides_local_total`, `ridefleet_rides_delegated_out_total` e `ridefleet_rides_received_delegation_total` evoluem, e o `ridefleet_service_state` alterna entre `0` e `1` conforme a carga.

---

## Resumo das entregas

| Requisito | Status |
|-----------|--------|
| Endpoint de métricas padrão Prometheus | ✅ `GET /metrics` |
| Corridas locais vs. delegadas para fora | ✅ dois counters |
| Corridas recebidas por delegação | ✅ counter com `origin_service` |
| Latência dos endpoints de corrida | ✅ histograma |
| Throughput (req/s) | ✅ counter HTTP + `rate()` |
| Estado do serviço (disponível/congestionado) | ✅ gauge `service_state` |
| Tamanho das filas de entrada e saída | ✅ gauge `queue_size` |
| Distribuição de carga entre instâncias | ✅ label `service_instance` |
| Métricas no Grafana do Core | ✅ job de scrape em `monitoring/` |
| Pipeline build → unit → integração → deploy | ✅ `ci.yml` |
| Testes de contrato do Core no pipeline | ✅ `contract-tests` |
| Deploy automático na branch principal | ✅ jobs `publish` + `deploy` |

> **Observação sobre o "padrão definido pelo Core":** os nomes das métricas seguem convenção Prometheus (`ridefleet_*`). Caso o material do Core fixe nomes/labels específicos, basta ajustar as strings em `app/core/metrics.py` — a estrutura permanece a mesma.