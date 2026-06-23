# Guia Docker — RideFleet (Core local e Core separado)

Guia para subir o backend do RideFleet em dois cenários e, principalmente, para
saber **exatamente o que mudar quando o IP da sua máquina ou do Core mudam** (ex.:
apresentar no laboratório, em casa, na rede da faculdade).

---

## 1. Visão geral

São dois stacks Docker independentes:

- **Core** (`ridefleet-core-sin142/infra/`): o servidor central de leilão/delegação. Publica a API em `8080`. É ele que **cria** a rede `ridefleet-net`.
- **RideFleet** (`SIN142-RideFleet/`): o seu backend (FastAPI + Postgres + Redis + nginx). O nginx publica em `8000`. Ele **entra** na `ridefleet-net` (declarada como `external: true`).

Duas variáveis controlam a comunicação e são as únicas que mudam entre cenários:

| Variável      | O que é                                                        |
|---------------|---------------------------------------------------------------|
| `CORE_URL`    | Onde **o seu backend** encontra o Core (para registrar e criar corridas). |
| `SERVICE_URL` | Onde **o Core** te chama de volta (callbacks do leilão: `/rides/incoming`, `/rides/{id}/assigned`). |

Elas vêm de um arquivo de ambiente, então trocar de cenário = trocar de arquivo:

```
.env          -> cópia do cenário ativo (o que 'docker compose up' usa sem flag)
.env.local    -> Core na mesma máquina
.env.externo  -> Core em outra máquina (você edita os IPs aqui)
```

---

## 2. Portas

| Serviço            | Porta no host | Observação                          |
|--------------------|---------------|-------------------------------------|
| Core (API)         | `8080`        | `POST /api/v1/rides`, `/groups/register` |
| RideFleet (nginx)  | `8000`        | sua API e os callbacks do Core      |

---

## 3. Cenário A — Core LOCAL (mesma máquina)

Aqui Core e RideFleet rodam no mesmo Docker. A ponte entre container e host é o
`host.docker.internal` (já resolve sozinho no Docker Desktop / Windows e Mac).
**Nada de IP** — esse cenário não muda de lugar para lugar.

### `.env.local`
```properties
CORE_URL=http://host.docker.internal:8080/api/v1
SERVICE_URL=http://host.docker.internal:8000
CORE_API_KEY=
```
> `CORE_API_KEY` vazio de propósito: o banco do Core local é zerado, então o seu
> backend se registra e recebe uma chave nova automaticamente no startup.

### Subir
```powershell
# 1) Core primeiro (ele cria a rede ridefleet-net)
cd ridefleet-core-sin142\infra
docker compose -f docker-compose.core.yml up -d

# 2) RideFleet (entra na rede que o Core criou)
cd ..\..\SIN142-RideFleet
docker compose --env-file .env.local up -d --build
```
> `docker compose up -d` sem flag também funciona, porque o `.env` padrão é cópia do local.

---

## 4. Cenário B — Core SEPARADO (outra máquina na rede)

Aqui a comunicação é por **IP de LAN**. Estes são os valores que mudam conforme o
lugar da apresentação.

### `.env.externo`
```properties
# IP da máquina onde roda o CORE (troque conforme o lugar)
CORE_URL=http://192.168.3.58:8080/api/v1
# IP DESTA máquina na LAN (o Core te chama de volta aqui)
SERVICE_URL=http://192.168.3.15:8000
CORE_API_KEY=
```

### Passos (1ª vez no local novo)
```powershell
cd SIN142-RideFleet

# 1) crie a rede (o Core remoto NÃO cria a rede na sua máquina)
docker network create ridefleet-net

# 2) suba apontando para o arquivo externo
docker compose --env-file .env.externo up -d --build
```

---

## 5. ⭐ O que mudar quando o IP varia (checklist de apresentação)

Quando você troca de lugar, **só o Cenário B** muda. Faça isto:

1. **Descubra o IP desta máquina** (a que roda o RideFleet):
   ```powershell
   (Get-NetIPConfiguration | Where-Object { $_.IPv4DefaultGateway }).IPv4Address.IPAddress
   ```
   ou `ipconfig` e pegue o "Endereço IPv4" do adaptador ativo (Wi-Fi/Ethernet).

2. **Descubra o IP do Core** — peça a quem está rodando o Core, ou rode na máquina dele o mesmo comando acima.

3. **Edite `SIN142-RideFleet/.env.externo`**, trocando os dois IPs:
   - `CORE_URL=http://<IP_DO_CORE>:8080/api/v1`
   - `SERVICE_URL=http://<IP_DESTA_MAQUINA>:8000`

4. **Libere a porta 8000 no firewall** (uma vez por máquina), senão o Core não te alcança:
   ```powershell
   # PowerShell como Administrador
   New-NetFirewallRule -DisplayName "RideFleet 8000" -Direction Inbound -LocalPort 8000 -Protocol TCP -Action Allow -Profile Any
   ```

5. **Garanta a rede** (uma vez): `docker network create ridefleet-net` (se já existir, o erro é inofensivo).

6. **Suba**: `docker compose --env-file .env.externo up -d --build`.

> Resumo: por local novo, só mexe em **2 linhas** do `.env.externo` (os IPs) e, na
> primeira vez naquela máquina, no **firewall** e na **rede**.

### Tabela rápida

| Item                     | Cenário A (local)                         | Cenário B (Core separado)                  |
|--------------------------|-------------------------------------------|--------------------------------------------|
| `CORE_URL`               | `http://host.docker.internal:8080/api/v1` | `http://<IP_CORE>:8080/api/v1`             |
| `SERVICE_URL`            | `http://host.docker.internal:8000`        | `http://<MEU_IP>:8000`                     |
| `CORE_API_KEY`           | vazio                                     | vazio                                       |
| Criar `ridefleet-net`?   | não (o Core local cria)                   | sim, 1x: `docker network create ridefleet-net` |
| Firewall porta 8000?     | não precisa                               | sim, 1x por máquina                         |
| Comando                  | `--env-file .env.local`                   | `--env-file .env.externo`                   |

---

## 6. Verificar se está funcionando

### 6.1 Backend de pé
```powershell
curl.exe http://localhost:8000/health
```
Deve responder com `available_drivers`, `queue`, etc.

### 6.2 Registro no Core
No log do backend deve aparecer o registro no Core:
```powershell
docker compose logs ridefleet-1 | Select-String "Registrado no Core"
```
Se não aparecer, o `CORE_URL` está errado ou o Core não está acessível.

### 6.3 (Cenário B) O Core te alcança?
Em **outra máquina** da rede (idealmente a do Core):
```powershell
curl.exe http://<MEU_IP>:8000/health
```
Se não responder de fora, é firewall (passo 4) ou IP errado.

### 6.4 Testar a delegação (receber corrida)
```powershell
# registre um grupo de ORIGEM e copie o apiKey
curl.exe -X POST http://<IP_CORE_ou_localhost>:8080/api/v1/groups/register -H "content-type: application/json" -d '{\"groupId\":\"origem-teste\",\"groupName\":\"Origem Teste\",\"serviceUrl\":\"http://origem-teste:9999\",\"contactEmail\":\"teste@ufv.br\"}'

# gere corridas como origem-teste (grupo diferente do seu -> você pode vencer e receber)
python scripts/load_test.py --core-endpoint http://<IP_CORE_ou_localhost>:8080/api/v1/rides --api-key <apiKey> --group origem-teste -n 5 -c 2 --watch-core
```
Com pelo menos um **motorista cadastrado**, o `--watch-core` deve mostrar o vencedor
como `ridefleet-grupo-a` e a corrida aparece no front com `recebida · origem-teste`.

---

## 7. Pontos que dependem do contrato do Core (não mudam com o IP)

O endpoint de criação de corrida é **`POST /api/v1/rides`** (sem barra no fim),
autenticado por **`X-API-Key`** (não Bearer), com corpo camelCase
(`originServiceId`, `passengerId`, `origin`/`destination` com `lat`/`lng`/`street`/
`number`/`city`/`state`, `logicalTimestamp`, `auctionTimeoutSeconds`). Use sempre o
`load_test.py` corrigido — a versão antiga (Bearer + `origem`/`destino` + barra final)
não funciona.

A chave de API é **por instância de Core**: a chave do Core remoto não vale no Core
local (e vice-versa), porque cada Core tem seu próprio banco. Por isso `CORE_API_KEY`
fica vazio e o registro é feito a cada subida.

---

## 8. Erros comuns e causa

| Sintoma                                                              | Causa / correção                                                                 |
|---------------------------------------------------------------------|----------------------------------------------------------------------------------|
| `307` ao criar corrida                                              | Endpoint com barra final + cliente sem follow-redirect. Use o `load_test.py` corrigido (`/rides` sem barra). |
| `401 Header X-API-Key ausente`                                     | Está usando Bearer; a rota de grupo exige `X-API-Key`. Registre o grupo e passe `--api-key`. |
| `422 ... auctionDeadline/lockExpiresAt Field required`             | São callbacks do Core para o serviço; o payload de teste precisa desses timestamps ISO. |
| Leilão dá `timeout` (proposta `status: timeout`)                  | O Core não conseguiu te chamar. Verifique `SERVICE_URL` (IP correto desta máquina) e o firewall da 8000. |
| `network ridefleet-net exists but was not created by compose`      | Rede criada na mão conflita com o Core (que a gerencia). Remova-a (`docker network rm ridefleet-net`) e suba o Core primeiro; no Cenário B, crie-a você e mantenha `external: true`. |
| Log não mostra "Registrado no Core"                               | `CORE_URL` errado ou Core inacessível. No Cenário A confira `host.docker.internal:8080`; no B, o IP do Core. |
| `version is obsolete` (warning)                                    | Inofensivo; pode remover a linha `version:` dos compose. |

---

## 9. Comandos úteis

```powershell
# trocar de cenário
docker compose --env-file .env.local   up -d --build   # Core local
docker compose --env-file .env.externo up -d --build   # Core separado

# logs
docker compose logs -f ridefleet-1
docker compose logs -f nginx           # ver chamadas do Core chegando

# reiniciar do zero (apaga banco -> registro e histórico zerados)
docker compose down -v
docker compose --env-file .env.local up -d --build

# conferir rede
docker network ls | findstr ridefleet-net

# derrubar
docker compose down
```
