import os
import psycopg


def get_dsn() -> str:
    dsn = os.environ.get("POSTGRES_DSN")
    if not dsn:
        raise RuntimeError("Missing POSTGRES_DSN")
    return dsn


def get_connection():
    return psycopg.connect(get_dsn())
