from typing import Literal, Optional

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool

from app.db import (
    add_task_db,
    delete_task_db,
    list_tasks_db,
    update_task_status_db,
)


def _get_user_id(config: RunnableConfig) -> int:
    user_id = config.get("configurable", {}).get("user_id")
    if user_id is None:
        raise ValueError("В config['configurable'] не передан user_id!")
    return int(user_id)


@tool
def list_tasks(config: RunnableConfig, status: Optional[str] = None) -> str:
    """
    Показывает список задач текущего пользователя с их номерами.
    Параметр status необязателен: можно передать 'todo', 'in_progress' или 'done'.
    Если нужно показать все задачи — не передавай status (оставь None).
    """
    user_id = _get_user_id(config)
    valid_statuses = {"todo", "in_progress", "done"}
    clean_status = status if status in valid_statuses else None

    tasks = list_tasks_db(user_id=user_id, status=clean_status)
    if not tasks:
        return "Список задач пуст."

    lines = []
    for t in tasks:
        lines.append(
            f"№{t['task_num']} | [{t['status']}] | Приоритет: {t['priority']} | "
            f"{t['title']} (описание: {t['description'] or 'нет'})"
        )
    return "\n".join(lines)


@tool
def create_task(
    config: RunnableConfig,
    title: str,
    description: str = "",
    priority: Literal["low", "medium", "high"] = "medium",
) -> str:
    """
    Создает новую задачу.
    title — короткое название задачи.
    description — подробное описание (по умолчанию пустое).
    priority — важность: 'low', 'medium' или 'high'.
    """
    user_id = _get_user_id(config)
    task = add_task_db(user_id=user_id, title=title, description=description, priority=priority)
    return f"Задача №{task['task_num']} создана: '{task['title']}' (приоритет: {task['priority']})."


@tool
def update_task_status(
    config: RunnableConfig,
    task_number: int,
    status: Literal["todo", "in_progress", "done"],
) -> str:
    """
    Меняет статус существующей задачи по её номеру (task_number).
    status может быть: 'todo', 'in_progress' или 'done'.
    """
    user_id = _get_user_id(config)
    task = update_task_status_db(user_id=user_id, task_num=task_number, status=status)
    if not task:
        return f"Задача №{task_number} не найдена."
    return f"Статус задачи №{task['task_num']} ('{task['title']}') изменен на '{task['status']}'."


@tool
def delete_task(config: RunnableConfig, task_number: int) -> str:
    """
    Полностью удаляет задачу по её номеру (task_number).
    """
    user_id = _get_user_id(config)
    deleted = delete_task_db(user_id=user_id, task_num=task_number)
    if not deleted:
        return f"Задача №{task_number} не найдена."
    return f"Задача №{task_number} удалена."


SAFE_TOOLS = [list_tasks]
SENSITIVE_TOOLS = [create_task, update_task_status, delete_task]
ALL_TOOLS = SAFE_TOOLS + SENSITIVE_TOOLS
SENSITIVE_TOOL_NAMES = {t.name for t in SENSITIVE_TOOLS}
