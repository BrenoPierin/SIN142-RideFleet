"""
Conexão assíncrona com PostgreSQL via SQLAlchemy.
Substitui o banco em memória da Semana 1.
"""
import os
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql+asyncpg://ridefleet:ridefleet123@db:5432/ridefleet")

engine = create_async_engine(DATABASE_URL, echo=os.getenv("SQL_ECHO", "false").lower() in ("1", "true", "yes"))

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


class Base(DeclarativeBase):
    pass


async def get_db():
    """Dependency do FastAPI — fornece sessão do banco por request."""
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def create_tables():
    """Cria todas as tabelas no banco (chamado no startup da aplicação)."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
