import asyncio
import html
import logging
import os
import uuid

from aiogram import Bot, Dispatcher, F
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command as TgCommand
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
)
from dotenv import load_dotenv
from langgraph.types import Command

from app.db import (
    delete_task_db,
    get_or_create_user_by_telegram,
    init_db,
    list_tasks_db,
    update_task_status_db,
)
from app.graph import graph

load_dotenv()
logger = logging.getLogger(__name__)
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("Не найден TELEGRAM_BOT_TOKEN в файле .env")

tg_proxy = os.getenv("TELEGRAM_PROXY")
session = AiohttpSession(proxy=tg_proxy) if tg_proxy else None
bot = Bot(token=BOT_TOKEN, session=session)
dp = Dispatcher()

ACTIVE_THREADS: dict[int, str] = {}

PRIORITY_LABELS = {
    "high": "Высокий",
    "medium": "Средний",
    "low": "Низкий",
}
STATUS_LABELS = {
    "todo": "К выполнению",
    "in_progress": "В работе",
    "done": "Готово",
}


def get_thread_config(chat_id: int, user_id: int) -> dict:
    thread_id = ACTIVE_THREADS.setdefault(chat_id, f"tg_{chat_id}")
    return {"configurable": {"thread_id": thread_id, "user_id": user_id}}


def main_menu_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(text="Мои задачи"),
                KeyboardButton(text="Код для сайта"),
            ],
            [KeyboardButton(text="Новый диалог")],
        ],
        resize_keyboard=True,
    )


def build_approval_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Подтвердить", callback_data="approve:yes"),
                InlineKeyboardButton(text="Отменить", callback_data="approve:no"),
            ]
        ]
    )


def format_action_human(act: dict) -> str:
    """Превращает технический вызов функции в понятное описание."""
    name = act["name"]
    args = act["args"]

    if name == "create_task":
        title = html.escape(str(args.get("title", "")))
        prio = PRIORITY_LABELS.get(args.get("priority", "medium"), "Средний")
        desc = args.get("description")
        extra = f"\n   <i>Описание: {html.escape(str(desc))}</i>" if desc else ""
        return f" <b>Создать задачу:</b> «{title}»\n   Приоритет: {prio}{extra}"

    if name == "update_task_status":
        tnum = args.get("task_number")
        st = STATUS_LABELS.get(args.get("status", ""), args.get("status"))
        return f"<b>Изменить статус задачи №{tnum}</b> → {st}"

    if name == "delete_task":
        tnum = args.get("task_number")
        return f"<b>Удалить задачу №{tnum}</b>"

    return f"<b>{html.escape(name)}</b>: <code>{html.escape(str(args))}</code>"


def build_tasks_view(user_id: int) -> tuple[str, InlineKeyboardMarkup]:
    """Формирует текст списка задач и быстрые инлайн-кнопки."""
    tasks = list_tasks_db(user_id=user_id)
    if not tasks:
        kb = InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="Обновить", callback_data="tasks_refresh")]]
        )
        return "<b>Список задач пока пуст.</b>\nНапиши мне, какую задачу добавить!", kb

    lines = ["<b>Твои задачи:</b>\n"]
    buttons = []

    for t in tasks:
        st_icon = {"todo": "⬜", "in_progress": "⏳", "done": "✅"}.get(t["status"], "⬜")
        pr_icon = {"high": "🔴", "medium": "🟡", "low": "🟢"}.get(t["priority"], "🟡")
        title = html.escape(t["title"])
        lines.append(f"{st_icon} <b>№{t['task_num']}</b> {pr_icon} {title}")

    active_tasks = [t for t in tasks if t["status"] != "done"][:6]
    for t in active_tasks:
        short_title = t["title"][:18] + ("…" if len(t["title"]) > 18 else "")
        buttons.append(
            [
                InlineKeyboardButton(
                    text=f"№{t['task_num']} {short_title}",
                    callback_data=f"task_done:{t['task_num']}",
                ),
                InlineKeyboardButton(
                    text=f" №{t['task_num']}",
                    callback_data=f"task_del:{t['task_num']}",
                ),
            ]
        )

    buttons.append([InlineKeyboardButton(text="Обновить", callback_data="tasks_refresh")])
    return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=buttons)


async def send_graph_result(chat_id: int, result: dict):
    interrupts = result.get("__interrupt__")
    if interrupts:
        payload = interrupts[0].value
        actions_text = [format_action_human(act) for act in payload.get("actions", [])]
        text = (
            "⏸ <b>Нужно твоё подтверждение:</b>\n\n"
            + "\n\n".join(actions_text)
            + "\n\n<i>Нажми кнопку ниже или напиши в чат, что исправить.</i>"
        )
        await bot.send_message(
            chat_id=chat_id,
            text=text,
            parse_mode="HTML",
            reply_markup=build_approval_keyboard(),
        )
        return

    reply = result["messages"][-1].content or "(пустой ответ)"
    await bot.send_message(chat_id=chat_id, text=reply)


@dp.message(TgCommand("start"))
async def cmd_start(message: Message):
    name = message.from_user.full_name if message.from_user else ""
    user = get_or_create_user_by_telegram(message.chat.id, name)
    await message.answer(
        f"Привет, <b>{html.escape(name or 'друг')}</b>! \n\n"
        "Я твой AI-менеджер задач. Просто пиши мне обычным текстом, что нужно сделать.\n\n"
        f"Твой код для синхронизации с сайтом:\n<code>{user['sync_code']}</code>",
        parse_mode="HTML",
        reply_markup=main_menu_keyboard(),
    )


@dp.message(TgCommand("code"))
@dp.message(F.text == "Код для сайта")
async def cmd_code(message: Message):
    name = message.from_user.full_name if message.from_user else ""
    user = get_or_create_user_by_telegram(message.chat.id, name)
    await message.answer(
        f"Твой код для входа в веб-интерфейс (нажми, чтобы скопировать):\n\n"
        f"<code>{user['sync_code']}</code>",
        parse_mode="HTML",
    )


@dp.message(TgCommand("tasks"))
@dp.message(F.text == "Мои задачи")
async def cmd_tasks(message: Message):
    name = message.from_user.full_name if message.from_user else ""
    user = get_or_create_user_by_telegram(message.chat.id, name)
    text, kb = build_tasks_view(user["id"])
    await message.answer(text, parse_mode="HTML", reply_markup=kb)


@dp.message(TgCommand("reset"))
@dp.message(F.text == "Новый диалог")
async def cmd_reset(message: Message):
    ACTIVE_THREADS[message.chat.id] = f"tg_{message.chat.id}_{uuid.uuid4().hex[:6]}"
    await message.answer("Память текущего диалога очищена! Начинаем с чистого листа.")


@dp.callback_query(F.data == "tasks_refresh")
async def cb_tasks_refresh(callback: CallbackQuery):
    await callback.answer("Обновлено")
    user = get_or_create_user_by_telegram(callback.message.chat.id)
    text, kb = build_tasks_view(user["id"])
    try:
        await callback.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
    except TelegramBadRequest:
        pass


@dp.callback_query(F.data.startswith("task_done:"))
async def cb_task_done(callback: CallbackQuery):
    task_id = int(callback.data.split(":")[1])
    user = get_or_create_user_by_telegram(callback.message.chat.id)
    update_task_status_db(user["id"], task_id, "done")
    await callback.answer(f"Задача №{task_id} выполнена!")
    text, kb = build_tasks_view(user["id"])
    try:
        await callback.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
    except TelegramBadRequest:
        pass


@dp.callback_query(F.data.startswith("task_del:"))
async def cb_task_del(callback: CallbackQuery):
    task_id = int(callback.data.split(":")[1])
    user = get_or_create_user_by_telegram(callback.message.chat.id)
    delete_task_db(user["id"], task_id)
    await callback.answer(f"Задача №{task_id} удалена!")
    text, kb = build_tasks_view(user["id"])
    try:
        await callback.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
    except TelegramBadRequest:
        pass


@dp.message(F.text)
async def handle_text(message: Message):
    chat_id = message.chat.id
    name = message.from_user.full_name if message.from_user else ""
    user = get_or_create_user_by_telegram(chat_id, name)
    config = get_thread_config(chat_id, user["id"])

    await bot.send_chat_action(chat_id=chat_id, action="typing")

    state = await asyncio.to_thread(graph.get_state, config)
    if state.next:
        result = await asyncio.to_thread(
            graph.invoke,
            Command(resume={"approved": False, "feedback": message.text}),
            config,
        )
    else:
        result = await asyncio.to_thread(
            graph.invoke,
            {"messages": [("user", message.text)]},
            config,
        )

    await send_graph_result(chat_id, result)


@dp.callback_query(F.data.startswith("approve:"))
async def handle_approval(callback: CallbackQuery):
    await callback.answer()
    chat_id = callback.message.chat.id
    user = get_or_create_user_by_telegram(chat_id)
    config = get_thread_config(chat_id, user["id"])

    state = await asyncio.to_thread(graph.get_state, config)
    if not state.next:
        await callback.message.edit_reply_markup(reply_markup=None)
        await callback.message.reply("Это действие уже неактуально.")
        return

    approved = callback.data == "approve:yes"
    status_label = "<i>Подтверждено</i>" if approved else "<i>Отменено</i>"

    await callback.message.edit_text(
        text=f"{callback.message.html_text}\n\n{status_label}",
        parse_mode="HTML",
        reply_markup=None,
    )

    await bot.send_chat_action(chat_id=chat_id, action="typing")

    resume_data = (
        {"approved": True}
        if approved
        else {"approved": False, "feedback": "Пользователь нажал кнопку Отменить."}
    )
    result = await asyncio.to_thread(graph.invoke, Command(resume=resume_data), config)
    await send_graph_result(chat_id, result)


async def main():
    init_db()
    logger.info("Telegram-бот запущен!")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
