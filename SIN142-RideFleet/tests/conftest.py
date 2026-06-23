"""
Configuração de testes.

Define variáveis de ambiente ANTES de qualquer import do app, para que o
engine do SQLAlchemy (criado no import de app.db.database) use SQLite local
em vez de Postgres. Assim os testes — inclusive os de contrato que sobem o
app via TestClient — rodam sem Postgres/Redis no ar.
"""
import os

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./test_ridefleet.db")
os.environ.setdefault("SERVICE_NAME", "ridefleet-grupo-a")
os.environ.setdefault("INSTANCE_ID", "test-instance")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379")
