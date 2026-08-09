"""Запуск бота «Помощник сантехника» (мессенджер MAX).

Режимы (переменная окружения BOT_MODE):
  polling  — long polling, для локального запуска и отладки (по умолчанию)
  webhook  — HTTP-сервер для вебхуков, для облака/VPS

Обязательная переменная: MAX_BOT_TOKEN — токен из @MasterBot в MAX.
Для webhook дополнительно: WEBHOOK_HOST (по умолчанию 0.0.0.0), WEBHOOK_PORT (8080).
Публичный HTTPS-адрес сервера регистрируется в MAX автоматически библиотекой
через подписку, либо вручную: см. README.
"""
import asyncio
import logging
import os
import sys

from maxapi import Bot

from . import db
from .handlers import dp
from .reminders import reminder_loop

logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s %(levelname)s %(name)s: %(message)s')
log = logging.getLogger('bot')


async def main():
    token = os.environ.get('MAX_BOT_TOKEN')
    if not token:
        sys.exit('Задайте переменную окружения MAX_BOT_TOKEN (токен бота из @MasterBot)')

    db.init()
    bot = Bot(token)
    mode = os.environ.get('BOT_MODE', 'polling').lower()
    asyncio.create_task(reminder_loop(bot))  # напоминания о сроках

    if mode == 'webhook':
        host = os.environ.get('WEBHOOK_HOST', '0.0.0.0')
        port = int(os.environ.get('WEBHOOK_PORT', '8080'))
        url = os.environ.get('WEBHOOK_URL')
        if url:
            await bot.delete_webhook()
            await bot.subscribe_webhook(url=url)
            log.info('Подписка на вебхук зарегистрирована: %s', url)
        else:
            log.warning('WEBHOOK_URL не задан — считаю, что подписка уже зарегистрирована в MAX')
        log.info('Запуск в режиме webhook на %s:%s', host, port)
        await dp.handle_webhook(bot=bot, host=host, port=port)
    else:
        await bot.delete_webhook()  # long polling не работает при активной подписке
        log.info('Запуск в режиме long polling')
        await dp.start_polling(bot)


if __name__ == '__main__':
    asyncio.run(main())
