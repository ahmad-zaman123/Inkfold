import socket

import pytest
from psycopg_pool import PoolTimeout

from app import store as store_module
from app.store import Store


def unused_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_a_failed_first_connection_is_retried_on_the_next_request(store, database_url, monkeypatch):
    # On Vercel a warm instance must recover once the database is reachable, not stay broken.
    monkeypatch.setattr(store_module, "POOL_OPEN_TIMEOUT", 1)
    broken = Store(f"postgresql://nobody@127.0.0.1:{unused_port()}/nothing?connect_timeout=1")
    for _ in range(2):
        with pytest.raises(PoolTimeout):
            broken.get_user("00000000-0000-0000-0000-000000000000")
    broken.database_url = database_url
    try:
        assert broken.get_user("00000000-0000-0000-0000-000000000000") is None
    finally:
        broken.close()
