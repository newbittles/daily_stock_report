from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from src.storage.db import get_connection, init_db


@pytest.fixture
def db_conn(tmp_path: Path) -> sqlite3.Connection:
    conn = get_connection(str(tmp_path / "test.db"))
    init_db(conn)
    yield conn
    conn.close()
