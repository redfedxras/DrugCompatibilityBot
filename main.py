"""
Точка входа. Инициализация происходит здесь, после загрузки .env.
Порядок: env → logging → сервисы → бот.
"""
import sys
import os
import asyncio
import logging
from pathlib import Path

from dotenv import load_dotenv

# Загружаем .env до любых импортов, которые читают os.getenv
_base = Path(__file__).parent
load_dotenv(_base / ".env")

from aiogram import Bot, Dispatcher
from bot_logic.handlers import router, set_checker
from services.checker import InteractionChecker

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


async def main() -> None:
    bot_token = os.getenv("BOT_TOKEN")
    pubmed_email = os.getenv("PUBMED_EMAIL")

    if not bot_token:
        logger.critical("BOT_TOKEN not set — check your .env file")
        sys.exit(1)
    if not pubmed_email:
        logger.critical("PUBMED_EMAIL not set — required by NCBI API policy")
        sys.exit(1)

    # Инициализируем сервисы после загрузки env
    checker = InteractionChecker(pubmed_email=pubmed_email)
    set_checker(checker)

    bot = Bot(token=bot_token)
    dp = Dispatcher()
    dp.include_router(router)

    logger.info("Bot starting...")
    await bot.delete_webhook(drop_pending_updates=True)

    try:
        await dp.start_polling(bot)
    finally:
        await bot.session.close()
        logger.info("Bot stopped.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        pass