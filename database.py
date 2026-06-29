import ssl
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.ext.declarative import declarative_base
from config import env

DATABASE_URL = env("DATABASE_URL", required=True)
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

connect_args: dict = {}
parsed_url = urlparse(DATABASE_URL)
query_params = dict(parse_qsl(parsed_url.query, keep_blank_values=True))
ssl_mode = query_params.get("sslmode", "").lower()

if ssl_mode in {"require", "verify-ca", "verify-full"}:
    ssl_context = ssl.create_default_context()
    if ssl_mode == "require":
        ssl_context.check_hostname = False
        ssl_context.verify_mode = ssl.CERT_NONE
    connect_args["ssl_context"] = ssl_context
    query_params.pop("sslmode", None)
    DATABASE_URL = urlunparse(
        parsed_url._replace(query=urlencode(query_params)),
    )

if DATABASE_URL.startswith("postgresql://"):
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+pg8000://", 1)

engine = create_engine(
    DATABASE_URL,
    connect_args=connect_args,
    pool_pre_ping=True,
    pool_recycle=300,
)
SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)

Base = declarative_base()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
