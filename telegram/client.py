import os
from dotenv import load_dotenv
from telethon import TelegramClient

load_dotenv()


def create_client():
    api_id = int(os.environ["TELEGRAM_API_ID"])
    api_hash = os.environ["TELEGRAM_API_HASH"]

    session = os.getenv(
        "TELEGRAM_SESSION",
        "gold_backtester"
    )

    return TelegramClient(
        session,
        api_id,
        api_hash
    )