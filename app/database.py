from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base
from sqlalchemy.pool import NullPool
from dotenv import load_dotenv
import os

# Load environment variables from .env (if present)
load_dotenv()

# Prefer an explicit DATABASE_URL env var; fall back to individual parts if not set.
DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    USER = os.getenv("user")
    PASSWORD = os.getenv("password")
    HOST = os.getenv("host")
    PORT = os.getenv("port") or "5432"
    DBNAME = os.getenv("dbname")

    missing = [n for n, v in [("user", USER), ("password", PASSWORD), ("host", HOST), ("dbname", DBNAME)] if not v]
    if missing:
        raise RuntimeError(
            "DATABASE_URL not set and missing required DB env vars: " + ", ".join(missing) + ".\n"
            "Set DATABASE_URL or provide user/password/host/dbname in your environment."
        )

    DATABASE_URL = f"postgresql+psycopg2://{USER}:{PASSWORD}@{HOST}:{PORT}/{DBNAME}?sslmode=require"

# Create the SQLAlchemy engine. `pool_pre_ping` helps with dropped connections.
# For serverless deployments you may prefer `poolclass=NullPool` (uncomment below).
engine = create_engine(DATABASE_URL, pool_pre_ping=True)
# Example serverless option:
# engine = create_engine(DATABASE_URL, poolclass=NullPool, connect_args={"sslmode": "require"})

# Session factory and declarative base exported for models to import
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# Helper function for manual health checks (does not run on import)
def test_connection(timeout_seconds: int = 5) -> bool:
    try:
        with engine.connect() as conn:
            return True
    except Exception:
        return False