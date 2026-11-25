import logging
import os
from pathlib import Path

from dotenv import load_dotenv
from langchain_ollama import ChatOllama
from langchain_openai import ChatOpenAI

# Загружаем переменные из .env
load_dotenv()
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
os.environ["NO_PROXY"] = "localhost,127.0.0.1"
os.environ["no_proxy"] = "localhost,127.0.0.1"

# Папка для хранения баз данных (задачи и чекпоинты LangGraph)
DATA_DIR = Path("data")
DATA_DIR.mkdir(exist_ok=True)

TASKS_DB_PATH = DATA_DIR / "tasks.db"
CHECKPOINTS_DB_PATH = DATA_DIR / "checkpoints.db"


def get_llm():
    """Создает и возвращает настроенную LLM в зависимости от .env."""
    provider = os.getenv("LLM_PROVIDER", "ollama").lower()

    if provider == "ollama":
        return ChatOllama(
            model=os.getenv("OLLAMA_MODEL", "qwen25_local"),
            base_url=os.getenv("OLLAMA_BASE_URL", "http://localhost:11434"),
            temperature=0,
            client_kwargs={"trust_env": False},
        )

    if provider == "deepseek":
        api_key = os.getenv("DEEPSEEK_API_KEY")
        if not api_key:
            raise ValueError("Не задан DEEPSEEK_API_KEY в файле .env")
        return ChatOpenAI(
            model=os.getenv("DEEPSEEK_MODEL", "deepseek-chat"),
            api_key=api_key,
            base_url="https://api.deepseek.com",
            temperature=0,
        )

    raise ValueError(f"Неизвестный LLM_PROVIDER: {provider}")
