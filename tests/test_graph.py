from langchain_core.messages import AIMessage, ToolMessage
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

import app.graph as graph_module
from app.db import add_task_db, get_or_create_user_by_telegram, list_tasks_db


class FakeLLM:
    """Заглушка вместо реальной Ollama: отдает заранее заданные ответы."""

    def __init__(self, responses: list[AIMessage]):
        self._responses = iter(responses)
        self.received_history: list[list] = []

    def invoke(self, messages):
        self.received_history.append(messages)
        return next(self._responses)


def test_safe_tool_executes_without_interrupt(temp_db, monkeypatch):
    user = get_or_create_user_by_telegram(2001, "Даша")
    add_task_db(user["id"], "Купить молоко", priority="low")

    fake_llm = FakeLLM(
        [
            AIMessage(
                content="",
                tool_calls=[{"name": "list_tasks", "args": {}, "id": "c1", "type": "tool_call"}],
            ),
            AIMessage(content="У тебя есть задача №1: Купить молоко."),
        ]
    )
    monkeypatch.setattr(graph_module, "llm_with_tools", fake_llm)
    test_graph = graph_module.build_graph(checkpointer=MemorySaver())

    cfg = {"configurable": {"thread_id": "t_safe", "user_id": user["id"]}}
    result = test_graph.invoke({"messages": [("user", "Покажи задачи")]}, cfg)

    assert "__interrupt__" not in result
    assert "Купить молоко" in result["messages"][-1].content


def test_sensitive_tool_pauses_and_executes_after_approve(temp_db, monkeypatch):
    user = get_or_create_user_by_telegram(2002, "Даша")

    fake_llm = FakeLLM(
        [
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "create_task",
                        "args": {"title": "Сдать отчёт", "priority": "high"},
                        "id": "c2",
                        "type": "tool_call",
                    }
                ],
            ),
            AIMessage(content="Готово, задача №1 создана!"),
        ]
    )
    monkeypatch.setattr(graph_module, "llm_with_tools", fake_llm)
    test_graph = graph_module.build_graph(checkpointer=MemorySaver())

    cfg = {"configurable": {"thread_id": "t_create", "user_id": user["id"]}}

    paused = test_graph.invoke({"messages": [("user", "Создай задачу Сдать отчёт")]}, cfg)
    assert "__interrupt__" in paused
    assert paused["__interrupt__"][0].value["actions"][0]["name"] == "create_task"
    assert list_tasks_db(user["id"]) == []

    resumed = test_graph.invoke(Command(resume={"approved": True}), cfg)
    assert "__interrupt__" not in resumed
    tasks = list_tasks_db(user["id"])
    assert len(tasks) == 1
    assert tasks[0]["title"] == "Сдать отчёт"
    assert tasks[0]["task_num"] == 1


def test_reject_with_feedback_cancels_action_and_notifies_agent(temp_db, monkeypatch):
    user = get_or_create_user_by_telegram(2003, "Даша")
    add_task_db(user["id"], "Важная задача")

    fake_llm = FakeLLM(
        [
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "delete_task",
                        "args": {"task_number": 1},
                        "id": "c3",
                        "type": "tool_call",
                    }
                ],
            ),
            AIMessage(content="Хорошо, не удаляю задачу №1."),
        ]
    )
    monkeypatch.setattr(graph_module, "llm_with_tools", fake_llm)
    test_graph = graph_module.build_graph(checkpointer=MemorySaver())

    cfg = {"configurable": {"thread_id": "t_reject", "user_id": user["id"]}}

    paused = test_graph.invoke({"messages": [("user", "Удали задачу 1")]}, cfg)
    assert "__interrupt__" in paused

    resumed = test_graph.invoke(
        Command(resume={"approved": False, "feedback": "Оставь её, передумала"}),
        cfg,
    )
    assert "__interrupt__" not in resumed

    assert len(list_tasks_db(user["id"])) == 1

    second_call_messages = fake_llm.received_history[1]
    tool_msgs = [m for m in second_call_messages if isinstance(m, ToolMessage)]
    assert len(tool_msgs) == 1
    assert "Оставь её, передумала" in tool_msgs[0].content
