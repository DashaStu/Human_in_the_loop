import pytest

import app.db as db_module


@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    """Создает изолированную тестовую SQLite-базу для каждого теста."""
    test_db_path = tmp_path / "test_tasks.db"
    monkeypatch.setattr(db_module, "TASKS_DB_PATH", test_db_path)
    db_module.init_db()
    return test_db_path
