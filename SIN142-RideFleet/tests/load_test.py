import asyncio
import time
import json
import argparse
from dataclasses import dataclass, field

try:
    import httpx
except ImportError:
    raise SystemExit("Instale a dependência: pip install httpx")


ENDPOINT = "http://localhost:8080/api/v1/rides/"

PAYLOAD = {
  "origem": {
    "latitude": -19.17,
    "longitude": -46.99,
    "rua": "Rua J",
    "numero": "123",
    "bairro": "Jardim Primavera",
    "cidade": "Rio Paranaíba",
    "cep": "38810-000",
    "estado": "MG"
  },
  "destino": {
    "latitude": -19.2,
    "longitude": -47.01,
    "rua": "UFV-Campus Rio Paranaíba",
    "numero": "35",
    "bairro": "Zona Rural",
    "cidade": "Rio Paranaíba",
    "cep": "38810-000",
    "estado": "MG"
  }
}

HEADERS = {
    "authorization": "Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIyIiwidGlwbyI6InBhc3NhZ2Vpcm8iLCJleHAiOjE3ODEzOTk2NDV9.F_6TNHD9cuLpvhKDqE_VagGkX_Ixuy9QNvFD2TrqcwE",
    "content-type": "application/json",
}

TOTAL_REQUESTS = 10
CONCURRENCY = 1         
TIMEOUT_SECONDS = 30

@dataclass
class Result:
    status: int = 0
    elapsed: float = 0.0
    error: str = ""


async def send_request(client: httpx.AsyncClient, index: int) -> Result:
    result = Result()
    start = time.perf_counter()
    try:
        response = await client.post(ENDPOINT, json=PAYLOAD)
        result.status = response.status_code
        result.elapsed = time.perf_counter() - start
        try:
            body = response.json()
            body_str = json.dumps(body, ensure_ascii=False, indent=2)
        except Exception:
            body_str = response.text
        print(f"[{index+1:>4}] {result.status} ({result.elapsed*1000:.0f}ms)\n{body_str}\n")
    except Exception as exc:
        result.elapsed = time.perf_counter() - start
        result.error = str(exc)
        print(f"[{index+1:>4}] ERRO ({result.elapsed*1000:.0f}ms): {exc}\n")
    return result


async def bounded_request(
    semaphore: asyncio.Semaphore,
    client: httpx.AsyncClient,
    index: int,
) -> Result:
    async with semaphore:
        return await send_request(client, index)


async def run(total: int, concurrency: int) -> None:
    semaphore = asyncio.Semaphore(concurrency)
    limits = httpx.Limits(max_connections=concurrency, max_keepalive_connections=concurrency)
    timeout = httpx.Timeout(TIMEOUT_SECONDS)

    print(f"\nEndpoint  : {ENDPOINT}")
    print(f"Payload   : {json.dumps(PAYLOAD, ensure_ascii=False)}")
    print(f"Total     : {total} requisições | Concorrência: {concurrency}\n")

    async with httpx.AsyncClient(limits=limits, timeout=timeout, headers=HEADERS) as client:
        wall_start = time.perf_counter()
        tasks = [bounded_request(semaphore, client, i) for i in range(total)]
        results: list[Result] = await asyncio.gather(*tasks)
        wall_elapsed = time.perf_counter() - wall_start

    # ── Estatísticas ─────────────────────────────────────────────────────────
    success = [r for r in results if r.status == 200 or (200 <= r.status < 300)]
    errors  = [r for r in results if r.error]
    other   = [r for r in results if r not in success and r not in errors]

    print("─" * 60)
    elapsed_times = [r.elapsed for r in results if not r.error]
    avg = sum(elapsed_times) / len(elapsed_times) if elapsed_times else 0
    mn  = min(elapsed_times, default=0)
    mx  = max(elapsed_times, default=0)

    # contagem por status code
    from collections import Counter
    status_counts = Counter(r.status for r in results if not r.error)

    print("── Resultado ──────────────────────────────────────────────────")
    print(f"  Tempo total      : {wall_elapsed:.2f}s")
    print(f"  Req/s            : {total / wall_elapsed:.1f}")
    print(f"  Bem-sucedidas    : {len(success)}")
    print(f"  Erros de rede    : {len(errors)}")
    print(f"  Outros status    : {len(other)}")
    print(f"  Latência média   : {avg * 1000:.1f} ms")
    print(f"  Latência mín/máx : {mn * 1000:.1f} / {mx * 1000:.1f} ms")
    print(f"\n  Status codes     : {dict(status_counts)}")

    if errors:
        print(f"\n  Primeiros erros:")
        for r in errors[:5]:
            print(f"    {r.error}")
    print()


# ── Entrada ──────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Load test de endpoint de corridas")
    parser.add_argument("-n", "--total",       type=int, default=TOTAL_REQUESTS, help="Total de requisições")
    parser.add_argument("-c", "--concurrency", type=int, default=CONCURRENCY,    help="Requisições simultâneas")
    args = parser.parse_args()

    asyncio.run(run(args.total, args.concurrency))
