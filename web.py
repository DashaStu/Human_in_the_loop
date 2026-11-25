import uuid

import streamlit as st
from langgraph.types import Command

from app.db import (
    delete_task_db,
    get_user_by_sync_code,
    init_db,
    list_tasks_db,
    update_task_status_db,
)
from app.graph import graph

st.set_page_config(page_title="AI Таск-менеджер", page_icon="✅", layout="wide")
init_db()


PRIORITY_BADGES = {
    "high": "Высокий",
    "medium": "Средний",
    "low": "Низкий",
}
STATUS_BADGES = {
    "todo": "К выполнению",
    "in_progress": "В работе",
    "done": "Готово",
}


def format_action_md(act: dict) -> str:
    name = act["name"]
    args = act["args"]
    if name == "create_task":
        prio = PRIORITY_BADGES.get(args.get("priority", "medium"), "Средний")
        desc = f"\n  - *Описание:* {args['description']}" if args.get("description") else ""
        return f"**Создать задачу:** «{args.get('title')}»\n  - *Приоритет:* {prio}{desc}"
    if name == "update_task_status":
        st_label = STATUS_BADGES.get(args.get("status", ""), args.get("status"))
        return f"**Изменить статус задачи №{args.get('task_number')}** → {st_label}"
    if name == "delete_task":
        return f"**Удалить задачу №{args.get('task_number')}**"
    return f"**{name}**: `{args}`"


with st.sidebar:
    st.title("Синхронизация")

    if "user" not in st.session_state:
        st.session_state.user = None
    if "web_thread_id" not in st.session_state:
        st.session_state.web_thread_id = f"web_{uuid.uuid4().hex[:8]}"

    if st.session_state.user is None:
        st.info("Отправь `/code` боту в Telegram, скопируй код и вставь сюда:")
        code_input = st.text_input("Код привязки", placeholder="Например: USER-1234")
        if st.button("Войти", type="primary", use_container_width=True):
            user = get_user_by_sync_code(code_input)
            if user:
                st.session_state.user = user
                st.session_state.web_thread_id = f"web_{user['id']}_{uuid.uuid4().hex[:6]}"
                st.rerun()
            else:
                st.error("Код не найден. Проверь код у бота командой /code.")
        st.stop()

    user = st.session_state.user
    st.success(f"Профиль: **{user['name'] or 'Telegram'}**\nКод: `{user['sync_code']}`")

    if st.button("Новый диалог с агентом", use_container_width=True):
        st.session_state.web_thread_id = f"web_{user['id']}_{uuid.uuid4().hex[:6]}"
        st.rerun()

    if st.button("Выйти", use_container_width=True):
        st.session_state.user = None
        st.rerun()


user = st.session_state.user
config = {
    "configurable": {
        "thread_id": st.session_state.web_thread_id,
        "user_id": user["id"],
    }
}

col_tasks, col_chat = st.columns([1.05, 1.15], gap="large")

with col_tasks:
    head_l, head_r = st.columns([3, 1])
    with head_l:
        st.subheader("Мои задачи")
    with head_r:
        if st.button("Обновить", use_container_width=True):
            st.rerun()

    filter_opt = st.radio(
        "Фильтр:",
        options=["Все", "Активные", "Готовые"],
        horizontal=True,
        label_visibility="collapsed",
    )

    all_tasks = list_tasks_db(user["id"])
    if filter_opt == "Активные":
        tasks = [t for t in all_tasks if t["status"] != "done"]
    elif filter_opt == "Готовые":
        tasks = [t for t in all_tasks if t["status"] == "done"]
    else:
        tasks = all_tasks

    if not tasks:
        st.info("Задач пока нет. Попроси ассистента справа создать первую!")
    else:
        for t in tasks:
            with st.container(border=True):
                prio = PRIORITY_BADGES.get(t["priority"], "🟡")
                stat = STATUS_BADGES.get(t["status"], "⬜")
                st.markdown(f"**№{t['task_num']} — {t['title']}**")
                if t["description"]:
                    st.caption(t["description"])
                st.caption(f"{stat}  |  {prio}  |  Создана: {t['created_at']}")

                b1, b2, b3 = st.columns(3)
                with b1:
                    if t["status"] != "in_progress":
                        if st.button("В работу", key=f"prog_{t['id']}", use_container_width=True):
                            update_task_status_db(user["id"], t["task_num"], "in_progress")
                            st.rerun()
                with b2:
                    if t["status"] != "done":
                        if st.button("Готово", key=f"done_{t['id']}", use_container_width=True):
                            update_task_status_db(user["id"], t["task_num"], "done")
                            st.rerun()
                    else:
                        if st.button("Вернуть", key=f"todo_{t['id']}", use_container_width=True):
                            update_task_status_db(user["id"], t["task_num"], "todo")
                            st.rerun()
                with b3:
                    if st.button("🗑 Удалить", key=f"del_{t['id']}", use_container_width=True):
                        delete_task_db(user["id"], t["task_num"])
                        st.rerun()


with col_chat:
    st.subheader("Чат с ассистентом")

    state = graph.get_state(config)
    messages = state.values.get("messages", []) if state.values else []

    chat_box = st.container(height=470)
    with chat_box:
        if not messages:
            st.caption(
                "Напиши, например: «Создай задачу Купить билеты с высоким приоритетом» или «Покажи мои задачи»."
            )
        for msg in messages:
            if msg.type == "human":
                with st.chat_message("user"):
                    st.write(msg.content)
            elif msg.type == "ai" and msg.content:
                with st.chat_message("assistant"):
                    st.write(msg.content)

    # Если граф на паузе — рисуем карточку подтверждения
    if state.next:
        last_ai_msg = messages[-1]
        pending_actions = getattr(last_ai_msg, "tool_calls", []) or []

        with st.container(border=True):
            st.warning("⏸ **Агент ждёт твоего подтверждения:**")
            for act in pending_actions:
                st.markdown(format_action_md(act))

            c1, c2 = st.columns(2)
            with c1:
                if st.button("Подтвердить", type="primary", use_container_width=True):
                    with st.spinner("Выполняю..."):
                        graph.invoke(Command(resume={"approved": True}), config)
                    st.rerun()
            with c2:
                if st.button("Отменить", use_container_width=True):
                    with st.spinner("Отменяю..."):
                        graph.invoke(
                            Command(
                                resume={
                                    "approved": False,
                                    "feedback": "Пользователь отменил действие.",
                                }
                            ),
                            config,
                        )
                    st.rerun()

            feedback_text = st.text_input(
                "Или напиши правку для агента:",
                placeholder="Например: Поменяй приоритет на низкий",
                key="feedback_input",
            )
            if st.button("↩ Отправить правку", use_container_width=True):
                if feedback_text.strip():
                    with st.spinner("Пересчитываю план..."):
                        graph.invoke(
                            Command(resume={"approved": False, "feedback": feedback_text.strip()}),
                            config,
                        )
                    st.rerun()
    else:
        prompt = st.chat_input("Напиши сообщение агенту...")
        if prompt:
            with st.spinner("Агент думает..."):
                graph.invoke({"messages": [("user", prompt)]}, config)
            st.rerun()
