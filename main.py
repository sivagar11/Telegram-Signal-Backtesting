import asyncio

from telegram.client import create_client
from telegram.extractor import extract_signals


async def main():
    client = create_client()

    async with client:
        print("Connected to Telegram")
        await extract_signals(
            client,
            limit=5000
        )


if __name__ == "__main__":
    asyncio.run(main())