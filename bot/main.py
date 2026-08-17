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
import time

from maxapi import Bot

from . import db, handlers, status
from .handlers import dp
from .reminders import reminder_loop

logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s %(levelname)s %(name)s: %(message)s')
log = logging.getLogger('bot')


MIN_DELAY = 5      # секунд до первой попытки после обрыва
MAX_DELAY = 300    # дальше не разгоняемся
STABLE = 60        # столько проработал — считаем связь восстановленной
HEARTBEAT = 300    # как часто писать в лог, что опрос жив


def watch_updates(bot):
    """Делает запросы к MAX видимыми в логах и на странице состояния.

    Библиотека молча глотает таймауты запроса обновлений: и «MAX отвечает,
    но событий нет», и «запрос завис навсегда» выглядят одинаково — полной
    тишиной. Отличить одно от другого было нечем, а это разные поломки.
    """
    fetch = bot.get_updates

    async def counted(*args, **kwargs):
        try:
            events = await fetch(*args, **kwargs)
        except Exception as e:
            status.note_fetch_error(e)
            raise
        if status.note_fetch(len(events.get('updates') or [])):
            log.info('MAX ответил на запрос обновлений — связь есть')
        return events

    bot.get_updates = counted


async def heartbeat():
    """Раз в несколько минут отмечается в логе: молчание бота теперь читаемо."""
    while True:
        await asyncio.sleep(HEARTBEAT)
        log.info('Пульс: %s', status.pulse())


async def poll_forever(bot):
    """Long polling, который переживает обрывы связи и ошибки MAX.

    Раньше любая ошибка убивала процесс: Railway перезапускал контейнер, и
    вместе с ботом уезжало мини-приложение. Теперь падает только цикл опроса,
    а поднимается сам, с нарастающей паузой.
    """
    delay = MIN_DELAY
    while True:
        started = time.monotonic()
        status.note_poll_start()
        try:
            await dp.start_polling(bot)
            log.warning('Опрос MAX завершился сам')
        except asyncio.CancelledError:
            raise
        except Exception as e:
            status.note_poll_error(e)
            log.exception('Опрос MAX прервался')

        # проработал достаточно долго — связь была, начинаем отсчёт заново
        if time.monotonic() - started > STABLE:
            delay = MIN_DELAY
        log.info('Продолжу опрос через %s с', delay)
        await asyncio.sleep(delay)
        delay = min(delay * 2, MAX_DELAY)


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
        status.note_me(me.username, me.user_id)
        log.info('Бот: %s (id %s)', me.username, me.user_id)
        if not me.username:
            log.warning('У бота нет username — кнопка мини-приложения показана не будет')
    except Exception as e:
        status.note_me(None, None, error=e)
        log.exception('Не удалось получить данные бота')

    mode = os.environ.get('BOT_MODE', 'polling').lower()
    watch_updates(bot)
    asyncio.create_task(reminder_loop(bot))  # напоминания о сроках
    asyncio.create_task(heartbeat())         # видно, что опрос жив

    # Мини-приложение отдаём сами, GitHub Pages не нужен. Railway обычно задаёт
    # PORT, но не всегда — тогда слушаем 8080: это порт, который Railway
    # пробует по умолчанию. В режиме webhook порт занят диспетчером.
    if mode != 'webhook':
        from .webapp import start as start_webapp
        port = int(os.environ.get('PORT') or 8080)
        try:
            await start_webapp(port)
        except Exception:
            log.exception('Не удалось поднять сервер мини-приложения — бот работает без него')
    else:
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
        # long polling не работает при активной подписке на вебхук; если снять
        # её не вышло (MAX недоступен, лимит), это не повод падать целиком
        try:
            await bot.delete_webhook()
        except Exception:
            log.warning('Не удалось снять подписку на вебхук, продолжаю', exc_info=True)
        log.info('Запуск в режиме long polling')
        await poll_forever(bot)


if __name__ == '__main__':
    asyncio.run(main())
