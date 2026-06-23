"""
Load test de DELEGAÇÃO (contrato de GRUPO do Core).

O endpoint POST /api/v1/rides exige X-API-Key (chave do grupo) e o payload
camelCase do contrato de grupo — o MESMO que o seu core_client usa para delegar:
  { originServiceId, passengerId, origin, destination, logicalTimestamp,
    auctionTimeoutSeconds }

Cada corrida criada abre um leilao no Core, que delega aos grupos. O script
faz a carga e DEPOIS comprova quantas delegacoes novas chegaram ao seu grupo
(corridas com delegated_from no seu servico).

Uso:
  python scripts/load_test.py -n 5 -c 2
  python scripts/load_test.py -n 100 -c 10
  python scripts/load_test.py --api-key <chave> --group <id-do-grupo>
"""
import asyncio
import time
import json
import argparse
from collections import Counter
from dataclasses import dataclass

try:
    import httpx
except ImportError:
    raise SystemExit("Instale a dependencia: pip install httpx")


# ── Core (contrato de GRUPO: X-API-Key + camelCase) ─────────────────────────
CORE_ENDPOINT = "http://localhost:8080/api/v1/rides"   # Core local (sem barra no fim!)
API_KEY  = "rfk_97b58fd30b3d1c76f5ddabe8dffe9190"   # passe com --api-key (vinda do /groups/register)
GROUP_ID = "group-01"   # grupo de ORIGEM (diferente do seu, p/ seu grupo poder vencer)

# ── Seu servico (para comprovar o recebimento da delegacao) ──────────────────
SERVICE_URL = "http://localhost:8000"

# Sub-objetos de localizacao (estrutura vista nos callbacks do Core)
ORIGIN = {"lat": -19.17, "lng": -46.99, "street": "Rua J", "number": "123",
          "city": "Rio Paranaiba", "state": "MG"}
DESTINATION = {"lat": -19.20, "lng": -47.01, "street": "UFV Rio Paranaiba", "number": "35",
               "city": "Rio Paranaiba", "state": "MG"}

TOTAL_REQUESTS = 100
CONCURRENCY = 10
TIMEOUT_SECONDS = 30


def build_payload(index: int) -> dict:
    return {
        "originServiceId": GROUP_ID,
        "passengerId": f"pax-load-{index}",
        "origin": ORIGIN,
        "destination": DESTINATION,
        "logicalTimestamp": index + 1,
        "auctionTimeoutSeconds": 10,
    }


@dataclass
class Result:
    status: int = 0
    elapsed: float = 0.0
    error: str = ""
    ride_id: str = ""


def _extract_id(body):
    if not isinstance(body, dict):
        return ""
    for k in ("rideUuid", "uuid", "id", "ride_id", "corridaId"):
        if body.get(k):
            return str(body[k])
    for wrap in ("ride", "corrida", "data"):
        inner = body.get(wrap)
        if isinstance(inner, dict):
            got = _extract_id(inner)
            if got:
                return got
    return ""


async def send_request(client: httpx.AsyncClient, index: int) -> Result:
    result = Result()
    start = time.perf_counter()
    try:
        response = await client.post(CORE_ENDPOINT, json=build_payload(index))
        result.status = response.status_code
        result.elapsed = time.perf_counter() - start
        try:
            body = response.json()
            result.ride_id = _extract_id(body)
            body_str = json.dumps(body, ensure_ascii=False)
        except Exception:
            body_str = response.text
        marca = "" if 200 <= result.status < 300 else "  <-- FALHOU"
        print(f"[{index+1:>4}] {result.status} ({result.elapsed*1000:.0f}ms) id={result.ride_id or '?'}{marca}")
        if not (200 <= result.status < 300):
            print(f"        {body_str[:300]}")
    except Exception as exc:
        result.elapsed = time.perf_counter() - start
        result.error = str(exc)
        print(f"[{index+1:>4}] ERRO ({result.elapsed*1000:.0f}ms): {exc}")
    return result


async def bounded_request(sem, client, index):
    async with sem:
        return await send_request(client, index)


async def count_delegated(service: str):
    try:
        async with httpx.AsyncClient(timeout=10, follow_redirects=True) as c:
            r = await c.get(f"{service}/rides/")
            rides = r.json()
        if isinstance(rides, list):
            deleg = [x for x in rides if isinstance(x, dict) and x.get("delegated_from")]
            return len(deleg), {x["id"] for x in deleg}
    except Exception as e:
        print(f"  (aviso: nao consegui ler {service}/rides/ — {e})")
    return None, set()


async def _get(client, url):
    try:
        r = await client.get(url)
        try:
            return r.status_code, r.json()
        except Exception:
            return r.status_code, r.text
    except Exception as e:
        return None, str(e)


async def watch_core(api_key, ride_ids, wait=12):
    """Consulta no Core o desfecho do leilao de cada corrida criada."""
    headers = {"X-API-Key": api_key}
    print(f"\n── Resultado do leilao NO CORE (apos ~{wait}s) ─────────────────")
    await asyncio.sleep(wait)
    winners = Counter()
    async with httpx.AsyncClient(timeout=10, follow_redirects=True, headers=headers) as c:
        for rid in ride_ids:
            _, status = await _get(c, f"{CORE_ENDPOINT}/{rid}/status")
            _, prop = await _get(c, f"{CORE_ENDPOINT}/{rid}/proposals")
            estado = winner = None
            if isinstance(status, dict):
                estado = status.get("state") or status.get("status")
                winner = (status.get("winnerServiceId") or status.get("assignedTo")
                          or status.get("winner") or status.get("winnerGroupId"))
            nprop = (len(prop) if isinstance(prop, list)
                     else len(prop.get("proposals", [])) if isinstance(prop, dict) else "?")
            if isinstance(prop, dict):
                winner = winner or prop.get("winnerServiceId") or prop.get("winner")
            winners[winner or "—(sem vencedor)"] += 1
            print(f"  {rid[:8]}  estado={estado}  propostas={nprop}  vencedor={winner or '—'}")
    print(f"\n  Vencedores: {dict(winners)}")
    print("  (se o vencedor for sempre seu proprio grupo, voce recebe; se for outro,"
          " a corrida foi delegada para ele; se '—', ninguem deu lance)")


async def run(total, concurrency, verify, watch):
    limits = httpx.Limits(max_connections=concurrency, max_keepalive_connections=concurrency)
    timeout = httpx.Timeout(TIMEOUT_SECONDS)
    headers = {"X-API-Key": API_KEY, "content-type": "application/json"}

    print(f"\nCore endpoint : {CORE_ENDPOINT}")
    print(f"Grupo (origin): {GROUP_ID}")
    print(f"Seu servico   : {SERVICE_URL}")
    print(f"Total         : {total} corridas | Concorrencia: {concurrency}\n")

    antes, ids_antes = (None, set())
    if verify:
        antes, ids_antes = await count_delegated(SERVICE_URL)
        print(f"Delegacoes no seu grupo ANTES: {antes if antes is not None else '?'}\n")

    async with httpx.AsyncClient(limits=limits, timeout=timeout, headers=headers,
                                 follow_redirects=True) as client:
        sem = asyncio.Semaphore(concurrency)
        wall_start = time.perf_counter()
        tasks = [bounded_request(sem, client, i) for i in range(total)]
        results = await asyncio.gather(*tasks)
        wall_elapsed = time.perf_counter() - wall_start

    success = [r for r in results if 200 <= r.status < 300]
    errors  = [r for r in results if r.error]
    status_counts = Counter(r.status for r in results if not r.error)
    elapsed_times = [r.elapsed for r in results if not r.error]
    avg = sum(elapsed_times) / len(elapsed_times) if elapsed_times else 0

    print("\n── Resultado da carga ──────────────────────────────────────────")
    print(f"  Tempo total      : {wall_elapsed:.2f}s")
    print(f"  Req/s            : {total / wall_elapsed:.1f}")
    print(f"  Criadas (2xx)    : {len(success)}")
    print(f"  Erros de rede    : {len(errors)}")
    print(f"  Latencia media   : {avg * 1000:.1f} ms")
    print(f"  Status codes     : {dict(status_counts)}")

    if status_counts and all(c in (401, 403) for c in status_counts):
        print("\n  >> 401/403: X-API-Key invalida/ausente OU originServiceId != grupo da chave.")
        print("     Confirme --api-key e --group (id registrado do seu grupo no Core).")
    if any(c == 422 for c in status_counts):
        print("\n  >> 422: payload nao bate com o contrato do Core (veja o corpo acima).")

    if errors:
        print("\n  Primeiros erros:")
        for r in errors[:5]:
            print(f"    {r.error}")

    if watch:
        ride_ids = [r.ride_id for r in results if r.ride_id]
        if ride_ids:
            await watch_core(API_KEY, ride_ids)

    if verify and antes is not None:
        print("\n── Delegacao para o seu grupo ──────────────────────────────────")
        print("  aguardando o Core leiloar e delegar (ate ~25s)...")
        depois, ids_depois = antes, ids_antes
        for _ in range(10):
            await asyncio.sleep(2.5)
            depois, ids_depois = await count_delegated(SERVICE_URL)
            if depois is not None and depois > antes:
                break
        novas = ids_depois - ids_antes
        print(f"  Delegacoes ANTES : {antes}")
        print(f"  Delegacoes DEPOIS: {depois}")
        print(f"  NOVAS no seu grupo: {len(novas)}  {'<-- delegacao funcionando!' if novas else ''}")
        if not novas and len(success) > 0:
            print("  Corridas criadas no Core, mas nada chegou ao seu grupo. Possiveis causas:")
            print("   - seu grupo nao esta registrado (ou SERVICE_URL != IP desta maquina);")
            print("   - sem motorista livre -> seu /rides/incoming recusa o leilao (204);")
            print("   - outro grupo venceu os leiloes;")
            print("   - firewall: o Core nao alcanca seu :8000 para os callbacks.")
    print()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Load test de delegacao (grupo -> Core -> seu grupo)")
    parser.add_argument("-n", "--total", type=int, default=TOTAL_REQUESTS, help="Total de corridas")
    parser.add_argument("-c", "--concurrency", type=int, default=CONCURRENCY, help="Corridas simultaneas")
    parser.add_argument("--core-endpoint", default=CORE_ENDPOINT, help="Endpoint de criacao de corrida no Core")
    parser.add_argument("--service", default=SERVICE_URL, help="URL do seu servico (verificacao)")
    parser.add_argument("--api-key", default=API_KEY, help="X-API-Key do seu grupo no Core")
    parser.add_argument("--group", default=GROUP_ID, help="originServiceId = id registrado do seu grupo")
    parser.add_argument("--no-verify", action="store_true", help="nao verifica a delegacao no seu servico")
    parser.add_argument("--watch-core", action="store_true", help="consulta no Core o vencedor de cada corrida criada")
    args = parser.parse_args()

    CORE_ENDPOINT = args.core_endpoint
    SERVICE_URL = args.service
    API_KEY = args.api_key
    GROUP_ID = args.group

    asyncio.run(run(args.total, args.concurrency, verify=not args.no_verify, watch=args.watch_core))