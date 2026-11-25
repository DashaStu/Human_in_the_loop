from app.db import (
    add_task_db,
    delete_task_db,
    get_or_create_user_by_telegram,
    get_user_by_sync_code,
    list_tasks_db,
    update_task_status_db,
)


def test_user_creation_and_sync_code(temp_db):
    user1 = get_or_create_user_by_telegram(1001, "Даша")
    # Повторный вызов с тем же telegram_id должен вернуть того же пользователя
    user1_again = get_or_create_user_by_telegram(1001, "Даша")
    assert user1["id"] == user1_again["id"]
    assert user1["sync_code"].startswith("USER-")

    # Поиск по коду не должен зависеть от пробелов и регистра
    found = get_user_by_sync_code(f"  {user1['sync_code'].lower()}  ")
    assert found is not None
    assert found["id"] == user1["id"]


def test_per_user_task_numbering(temp_db):
    u1 = get_or_create_user_by_telegram(1001, "Даша")
    u2 = get_or_create_user_by_telegram(1002, "Маша")

    t1 = add_task_db(u1["id"], "Задача Даши 1")
    t2 = add_task_db(u1["id"], "Задача Даши 2")
    t_masha = add_task_db(u2["id"], "Задача Маши 1")

    assert t1["task_num"] == 1
    assert t2["task_num"] == 2
    # У Маши своя нумерация с 1!
    assert t_masha["task_num"] == 1


def test_delete_does_not_renumber_existing_tasks(temp_db):
    u = get_or_create_user_by_telegram(1001, "Даша")
    add_task_db(u["id"], "Первая")
    add_task_db(u["id"], "Вторая")
    add_task_db(u["id"], "Третья")

    assert delete_task_db(u["id"], 2) is True
    t4 = add_task_db(u["id"], "Четвёртая")
    assert t4["task_num"] == 4

    nums = [t["task_num"] for t in list_tasks_db(u["id"])]
    assert nums == [1, 3, 4]


def test_user_isolation(temp_db):
    u1 = get_or_create_user_by_telegram(1001, "Даша")
    u2 = get_or_create_user_by_telegram(1002, "Хакер")

    add_task_db(u1["id"], "Секретная задача")

    # Другой пользователь не видит, не может обновить и не может удалить задачу №1
    assert list_tasks_db(u2["id"]) == []
    assert update_task_status_db(u2["id"], 1, "done") is None
    assert delete_task_db(u2["id"], 1) is False

    # У Даши задача осталась нетронутой
    dasha_tasks = list_tasks_db(u1["id"])
    assert len(dasha_tasks) == 1
    assert dasha_tasks[0]["status"] == "todo"
