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

from . import db, handlers
from .handlers import dp
from .reminders import reminder_loop

logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s %(levelname)s %(name)s: %(message)s')
log = logging.getLogger('bot')


async def main():
    token = os.environ.get('MAX_BOT_TOKEN')
    if not token:
        sys.exit('Задайте переменную окружения MAX_BOT_TOKEN (токен бота из @MasterBot)')

    log.info('Версия сборки: %s', handlers.build_version())

    db.init()
    bot = Bot(token)

    # Данные бота нужны для кнопки мини-приложения (MAX открывает его по username бота)
    try:
        me = await bot.get_me()
        handlers.BOT_ME.update(username=me.username, user_id=me.user_id)
        log.info('Бот: %s (id %s)', me.username, me.user_id)
        if not me.username:
            log.warning('У бота нет username — кнопка мини-приложения показана не будет')
    except Exception:
        log.exception('Не удалось получить данные бота')

    mode = os.environ.get('BOT_MODE', 'polling').lower()
    asyncio.create_task(reminder_loop(bot))  # напоминания о сроках

    # Мини-приложение отдаём сами: Railway задаёт PORT, GitHub Pages не нужен.
    # В режиме webhook порт занят диспетчером, поэтому там не поднимаем.
    port = os.environ.get('PORT')
    if port and mode != 'webhook':
        from .webapp import start as start_webapp
        try:
            await start_webapp(int(port))
        except Exception:
            log.exception('Не удалось поднять сервер мини-приложения — бот работает без него')
    elif port:
        log.info('Режим webhook: мини-приложение с этого порта не отдаётся')

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
