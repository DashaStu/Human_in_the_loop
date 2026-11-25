import logging
import sqlite3
from typing import Annotated, Literal, TypedDict

from langchain_core.messages import AnyMessage, SystemMessage, ToolMessage
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode
from langgraph.types import Command, interrupt

from app.config import CHECKPOINTS_DB_PATH, get_llm
from app.tools import ALL_TOOLS, SAFE_TOOLS, SENSITIVE_TOOL_NAMES

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """Ты — умный персональный ассистент для управления задачами.
Всегда общайся с пользователем вежливо и на русском языке.

Правила работы с инструментами:
1. Если пользователь просит удалить или обновить задачу, но не назвал её номер,
   СНАЧАЛА вызови list_tasks, посмотри список задач, найди нужный номер, и только потом вызывай delete_task или update_task_status. Никогда не выдумывай номер.
2. В ответах пользователю никогда не называй номер задачи словом "ID" — говори просто "номер" или "№" (например: "Задача №1").
3. Если пользователь отклонил выполнение действия и оставил комментарий — прислушайся к нему и либо предложи новый вариант, либо подтверди отмену.
"""


# 1. Описываем State (рюкзак с данными, который ходит по графу)
class AgentState(TypedDict):
    messages: Annotated[list[AnyMessage], add_messages]


# Подключаем инструменты к нашей LLM
llm = get_llm()
llm_with_tools = llm.bind_tools(ALL_TOOLS)


# 2. Описываем Узлы (Nodes)
def agent_node(state: AgentState) -> dict:
    """Узел мозга: LLM читает историю и решает — ответить текстом или вызвать инструмент."""
    messages = [SystemMessage(content=SYSTEM_PROMPT)] + state["messages"]
    response = llm_with_tools.invoke(messages)
    logger.info("Agent tool_calls=%s, text=%r", response.tool_calls, response.content)
    return {"messages": [response]}


def human_review_node(
    state: AgentState,
) -> Command[Literal["sensitive_tools", "agent"]]:
    """
    Узел проверки человеком (Human-in-the-loop).
    Ставит граф на паузу, спрашивает разрешение и направляет поток дальше.
    """
    last_message = state["messages"][-1]

    # Собираем понятный список действий, которые хочет сделать агент
    pending_actions = [
        {"id": tc["id"], "name": tc["name"], "args": tc["args"]} for tc in last_message.tool_calls
    ]

    # ПАУЗА: граф сохраняется в SQLite и ждет вызова Command(resume=...)
    decision = interrupt(
        {
            "message": "Агент запрашивает разрешение на выполнение действий:",
            "actions": pending_actions,
        }
    )
    logger.info("Human review decision=%s", decision)

    # Когда граф разбудят, выполнение продолжится с этой строчки,
    # а в переменной decision окажется то, что передал пользователь.
    if decision.get("approved"):
        return Command(goto="sensitive_tools")

    # Если пользователь отклонил действие — формируем ToolMessage с причиной отказа,
    # чтобы LLM поняла, что действие НЕ выполнилось, и учла комментарий.
    feedback = decision.get("feedback") or "Пользователь отклонил действие."
    rejection_messages = [
        ToolMessage(
            content=f"Действие отменено пользователем. Причина/комментарий: {feedback}",
            tool_call_id=tc["id"],
        )
        for tc in last_message.tool_calls
    ]
    return Command(goto="agent", update={"messages": rejection_messages})


# Готовые узлы из LangGraph для автоматического запуска Python-функций
safe_tools_node = ToolNode(SAFE_TOOLS)
# В sensitive_tools передаем ALL_TOOLS на случай, если LLM за один ход вызовет и safe, и sensitive инструмент вместе
sensitive_tools_node = ToolNode(ALL_TOOLS)


# 3. Описываем Условный переход (Роутер) после ответа агента
def route_after_agent(
    state: AgentState,
) -> Literal["safe_tools", "human_review", "__end__"]:
    last_message = state["messages"][-1]

    # Если инструментов вызывать не нужно — заканчиваем ход, отдаем текст юзеру
    if not getattr(last_message, "tool_calls", None):
        return END

    # Проверяем, есть ли среди вызванных инструментов хоть один опасный
    called_names = {tc["name"] for tc in last_message.tool_calls}
    if called_names & SENSITIVE_TOOL_NAMES:
        return "human_review"

    return "safe_tools"


# 4. Собираем сам Граф
def build_graph(checkpointer=None):
    builder = StateGraph(AgentState)

    # Добавляем узлы
    builder.add_node("agent", agent_node)
    builder.add_node("safe_tools", safe_tools_node)
    builder.add_node("human_review", human_review_node)
    builder.add_node("sensitive_tools", sensitive_tools_node)

    # Связываем узлы стрелочками
    builder.add_edge(START, "agent")
    builder.add_conditional_edges(
        "agent",
        route_after_agent,
        {
            "safe_tools": "safe_tools",
            "human_review": "human_review",
            END: END,
        },
    )
    builder.add_edge("safe_tools", "agent")
    builder.add_edge("sensitive_tools", "agent")
    # Из human_review стрелки не рисуем — ими управляет Command(goto=...) внутри самого узла

    # Подключаем персистентную память в SQLite
    if checkpointer is None:
        conn = sqlite3.connect(str(CHECKPOINTS_DB_PATH), check_same_thread=False, timeout=10.0)
        conn.execute("PRAGMA journal_mode=WAL;")
        checkpointer = SqliteSaver(conn)

    return builder.compile(checkpointer=checkpointer)


# Единый скомпилированный граф для использования в API и CLI
graph = build_graph()
