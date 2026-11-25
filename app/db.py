import secrets
import sqlite3
from datetime import datetime
from typing import Optional

from app.config import TASKS_DB_PATH


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(TASKS_DB_PATH, timeout=10.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


def init_db() -> None:
    """Создает таблицы users и tasks и включает режим WAL для параллельной работы."""
    with get_connection() as conn:
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                telegram_id INTEGER UNIQUE,
                sync_code TEXT UNIQUE NOT NULL,
                name TEXT DEFAULT '',
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                task_num INTEGER NOT NULL,
                title TEXT NOT NULL,
                description TEXT DEFAULT '',
                priority TEXT DEFAULT 'medium',
                status TEXT DEFAULT 'todo',
                created_at TEXT NOT NULL,
                UNIQUE(user_id, task_num),
                FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
            )
            """
        )
        conn.commit()


def _generate_unique_code(conn: sqlite3.Connection) -> str:
    while True:
        code = f"USER-{secrets.randbelow(9000) + 1000}"
        exists = conn.execute("SELECT 1 FROM users WHERE sync_code = ?", (code,)).fetchone()
        if not exists:
            return code


def get_or_create_user_by_telegram(telegram_id: int, name: str = "") -> dict:
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM users WHERE telegram_id = ?", (telegram_id,)).fetchone()
        if row:
            return dict(row)

        code = _generate_unique_code(conn)
        now = datetime.now().strftime("%Y-%m-%d %H:%M")
        cursor = conn.execute(
            """
            INSERT INTO users (telegram_id, sync_code, name, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (telegram_id, code, name, now),
        )
        conn.commit()
        new_row = conn.execute("SELECT * FROM users WHERE id = ?", (cursor.lastrowid,)).fetchone()
        return dict(new_row)


def get_user_by_sync_code(sync_code: str) -> Optional[dict]:
    clean_code = sync_code.strip().upper()
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM users WHERE sync_code = ?", (clean_code,)).fetchone()
        return dict(row) if row else None


def list_tasks_db(user_id: int, status: Optional[str] = None) -> list[dict]:
    """Возвращает задачи конкретного пользователя, отсортированные по номеру."""
    with get_connection() as conn:
        if status:
            rows = conn.execute(
                "SELECT * FROM tasks WHERE user_id = ? AND status = ? ORDER BY task_num ASC",
                (user_id, status),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM tasks WHERE user_id = ? ORDER BY task_num ASC",
                (user_id,),
            ).fetchall()
        return [dict(row) for row in rows]


def add_task_db(
    user_id: int,
    title: str,
    description: str = "",
    priority: str = "medium",
) -> dict:
    """Создает новую задачу со следующим личным номером для пользователя."""
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    with get_connection() as conn:
        next_row = conn.execute(
            "SELECT COALESCE(MAX(task_num), 0) + 1 AS next_num FROM tasks WHERE user_id = ?",
            (user_id,),
        ).fetchone()
        task_num = next_row["next_num"]

        cursor = conn.execute(
            """
            INSERT INTO tasks (user_id, task_num, title, description, priority, status, created_at)
            VALUES (?, ?, ?, ?, ?, 'todo', ?)
            """,
            (user_id, task_num, title, description, priority, now),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM tasks WHERE id = ?", (cursor.lastrowid,)).fetchone()
        return dict(row)


def update_task_status_db(user_id: int, task_num: int, status: str) -> Optional[dict]:
    """Обновляет статус задачи по её личному номеру у пользователя."""
    with get_connection() as conn:
        conn.execute(
            "UPDATE tasks SET status = ? WHERE user_id = ? AND task_num = ?",
            (status, user_id, task_num),
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM tasks WHERE user_id = ? AND task_num = ?",
            (user_id, task_num),
        ).fetchone()
        return dict(row) if row else None


def delete_task_db(user_id: int, task_num: int) -> bool:
    """Удаляет задачу по её личному номеру у пользователя."""
    with get_connection() as conn:
        cursor = conn.execute(
            "DELETE FROM tasks WHERE user_id = ? AND task_num = ?",
            (user_id, task_num),
        )
        conn.commit()
        return cursor.rowcount > 0
