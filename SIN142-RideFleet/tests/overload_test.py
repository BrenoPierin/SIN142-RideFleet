#!/usr/bin/env python3
"""
Teste de carga / overload ao vivo — RideFleet.

Roda contra o sistema NO AR (Docker) para a demonstração: cadastra 26 motoristas
(capacidade local), dispara 28 corridas, atende 26 localmente e deixa as 2
restantes caírem no overflow → delegação ao Core. Serve para popular o dashboard
Grafana do Core em tempo real.

Uso (com o stack no ar e o venv ativado):

    python scripts/overload_test.py
    python scripts/overload_test.py http://localhost:8000      # base alternativa

Dica: para um resultado limpo (exatamente 2 delegadas), suba o stack zerado:
    docker compose down -v && docker compose up -d --build
"""
import sys
import time

import httpx

API_BASE = (sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8000").rstrip("/")
CAPACIDADE = 26
TOTAL_CORRIDAS = 28
TIMEOUT = 10.0


def main() -> int:
    print(f"== Teste de overload — {API_BASE} ==\n")
    with httpx.Client(base_url=API_BASE, timeout=TIMEOUT) as c:
        # 0. Sanidade: serviço no ar?
        try:
            h = c.get("/health").json()
        except Exception as e:
            print(f"ERRO: não consegui falar com {API_BASE}/health — {e}")
            print("O stack está no ar? (docker compose ps)")
            return 1
        print(f"health: {h.get('status')} | motoristas livres no início: "
              f"{h.get('available_drivers')}")
        if (h.get("available_drivers") or 0) > 0:
            print("AVISO: já existem motoristas livres de execuções anteriores; "
                  "o número de delegadas pode não ser exatamente 2.\n"
                  "       Suba zerado com: docker compose down -v && up -d\n")

        # 1. Cadastra 26 motoristas (capacidade local)
        print(f"\n[1] Cadastrando {CAPACIDADE} motoristas...")
        for i in range(CAPACIDADE):
            c.post("/drivers/", json={
                "name": f"Motorista {i:02d}",
                "license_plate": f"OVR-{i:04d}",
                "phone": "000",
            })
        print(f"    {CAPACIDADE} motoristas cadastrados.")

        # 2. Dispara 28 corridas; atende localmente enquanto houver motorista livre
        print(f"\n[2] Enviando {TOTAL_CORRIDAS} corridas...")
        locais = 0
        delegadas = 0
        for i in range(TOTAL_CORRIDAS):
            pax = c.post("/passengers/", json={
                "name": f"Passageiro {i:02d}", "phone": "000"
            }).json()
            ride = c.post("/rides/", json={
                "passenger_id": pax["id"],
                "origin": "Centro",
                "destination": "Aeroporto",
            }).json()

            disponiveis = c.get("/drivers/available").json()
            if isinstance(disponiveis, list) and disponiveis:
                # atende localmente → consome um motorista
                c.patch(f"/rides/{ride['id']}/status", json={
                    "new_status": "match",
                    "driver_id": disponiveis[0]["id"],
                })
                locais += 1
                marca = "local"
            else:
                # sem motorista → o servidor já enfileirou na outbox (overflow)
                delegadas += 1
                marca = ">>> OVERFLOW (vai pro Core)"
            print(f"    corrida {i+1:02d}/{TOTAL_CORRIDAS}: {marca}")
            time.sleep(0.05)

        # 3. Confere o estado de overflow no servidor
        ov = c.get("/rides/overflow/check").json()

    print("\n== Resumo ==")
    print(f"  Atendidas localmente : {locais}")
    print(f"  Delegadas ao Core    : {delegadas}   (esperado: "
          f"{TOTAL_CORRIDAS - CAPACIDADE})")
    print(f"  Fila de saída (outbox): {ov.get('queue', {}).get('outbox')}")
    print(f"  should_delegate       : {ov.get('should_delegate')}")
    print("\nConfira as métricas:")
    print(f"  docker compose exec ridefleet-1 curl -s http://localhost:8000/metrics "
          f"| Select-String ridefleet_rides")
    print("  e o dashboard Grafana do Core (séries ridefleet_rides_*).")

    ok = delegadas == (TOTAL_CORRIDAS - CAPACIDADE)
    print(f"\nResultado: {'OK' if ok else 'DIVERGENTE'}")
    return 0 if ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
