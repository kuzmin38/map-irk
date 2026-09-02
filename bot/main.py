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
from maxapi.types import BotCommand

from . import backup, db, handlers, maxfix, razbor, status
from .handlers import dp
from .reminders import asked_loop, reminder_loop

logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s %(levelname)s %(name)s: %(message)s')
log = logging.getLogger('bot')


MIN_DELAY = 5      # секунд до первой попытки после обрыва
MAX_DELAY = 300    # дальше не разгоняемся
STABLE = 60        # столько проработал — считаем связь восстановленной
HEARTBEAT = 300    # как часто писать в лог, что опрос жив
POLL_TIMEOUT = 30  # столько MAX держит запрос, если событий нет
MIN_INTERVAL = 0.5  # не чаще двух запросов в секунду: у MAX предел пять


def _kinds(updates) -> list:
    """Типы пришедших событий. Ради лога опрос ронять нельзя, поэтому любую
    неожиданную форму ответа показываем как есть, а не разбираем."""
    return [str(u.get('update_type', '?')) if isinstance(u, dict) else str(u)[:40]
            for u in updates]


def watch_updates(bot):
    """Делает запросы к MAX видимыми в логах и не даёт им частить.

    Две беды сразу. Первая: библиотека молча глотает таймауты запроса
    обновлений — и «MAX отвечает, но событий нет», и «запрос завис навсегда»
    выглядят одинаково, полной тишиной. Вторая: без явного timeout MAX иногда
    отвечает мгновенно, цикл опроса разгоняется и ловит 429, а каждый такой
    отказ стоит пяти секунд простоя — то есть прямой задержки для людей.
    """
    fetch = bot.get_updates
    last = 0.0

    async def counted(*args, **kwargs):
        nonlocal last
        kwargs.setdefault('timeout', POLL_TIMEOUT)
        pause = MIN_INTERVAL - (time.monotonic() - last)
        if pause > 0:
            await asyncio.sleep(pause)
        last = time.monotonic()
        try:
            events = await fetch(*args, **kwargs)
        except Exception as e:
            status.note_fetch_error(e)
            raise
        spent = time.monotonic() - last
        updates = events.get('updates') or []
        # Что именно прислал MAX — до того, как за это возьмётся библиотека.
        # Событие неизвестного ей типа она пропускает, и сообщение исчезает
        # бесследно; здесь оно останется видимым в любом случае.
        if updates:
            log.info('MAX прислал событий: %d (%s), маркер %s',
                     len(updates), ', '.join(_kinds(updates)),
                     events.get('marker'))
        if status.note_fetch(len(updates), instant=spent < 1):
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


async def register_commands(bot):
    """Отдаёт MAX список команд быстрого меню.

    Клавиатура в MAX привязана к сообщению: чтобы вернуться к счётчикам,
    приходится крутить ленту и искать нужное сообщение. Команды же всегда
    лежат под полем ввода, и до любого экрана оттуда один шаг.

    Имена русские — читать «/счетчики» в меню проще, чем «/schet». Примет
    ли MAX кириллицу, в документации не сказано; если нет — отправляем те же
    команды латиницей, они работают наравне.
    """
    russkie = [(name, text) for name, text, _ in handlers.QUICK_COMMANDS]
    latinicey = [(handlers.ALIASES[name][-1], text) for name, text in russkie]
    for spisok, kak in ((russkie, 'по-русски'), (latinicey, 'латиницей')):
        try:
            await bot.set_commands(*[BotCommand(name=n, description=d)
                                     for n, d in spisok])
            log.info('Команды бота зарегистрированы (%s): %d', kak, len(spisok))
            return
        except Exception:
            log.warning('MAX не принял команды %s', kak, exc_info=True)
    # Меню — удобство, а не работа бота: команды всё равно наберутся руками
    log.warning('Быстрое меню в MAX не зарегистрировано — команды работают вручную')


def webhook_secret() -> str:
    """Секрет, которым MAX подписывает свои запросы к нам.

    Берём из переменной, а если её нет — из токена бота: он всё равно
    секретный, а лишняя настройка на боевом сервере это лишний повод
    забыть её задать.
    """
    import hashlib

    zadan = os.environ.get('WEBHOOK_SECRET')
    if zadan:
        return zadan
    token = os.environ.get('MAX_BOT_TOKEN') or 'lusya'
    return hashlib.sha256(token.encode()).hexdigest()[:32]


async def podpisatsya(bot, url: str):
    """Регистрирует подписку на вебхук, сняв прежние.

    MAX подписки копит, а не заменяет, и снимает их сам после нескольких
    часов неудачных доставок. Поэтому переустанавливаем при каждом старте
    и чистим старые.
    """
    try:
        await bot.delete_webhook()
    except Exception:
        log.warning('Не удалось снять прежние подписки', exc_info=True)
    try:
        await bot.subscribe_webhook(url=url, secret=webhook_secret())
        log.info('Подписка на вебхук зарегистрирована: %s', url)
        return True
    except Exception:
        log.exception('Не удалось зарегистрировать подписку на вебхук')
        return False


async def storozh_podpiski(bot, url: str, minut: int = 30):
    """Раз в полчаса проверяет, жива ли подписка, и восстанавливает её.

    Подписка пропадает сама, а тишина в чате выглядит как «бот сломался».
    Дешевле проверять, чем узнавать об этом от людей.
    """
    while True:
        await asyncio.sleep(minut * 60)
        try:
            est = await bot.get_subscriptions()
            adresa = [getattr(s, 'url', None) for s in
                      (getattr(est, 'subscriptions', None) or [])]
            if url in adresa:
                continue
            log.warning('Подписка на вебхук пропала — регистрирую заново')
            await podpisatsya(bot, url)
        except Exception:
            log.warning('Не удалось проверить подписку', exc_info=True)


async def main():
    token = os.environ.get('MAX_BOT_TOKEN')
    if not token:
        sys.exit('Задайте переменную окружения MAX_BOT_TOKEN (токен бота из @MasterBot)')

    log.info('Версия сборки: %s', handlers.build_version())

    maxfix.install()      # библиотека теряет события, которые не разобрала
    db.init()
    bot = Bot(token)

    # Данные бота нужны для кнопки мини-приложения (MAX открывает его по username бота)
    try:
        me = await bot.get_me()
        handlers.BOT_ME.update(username=me.username, user_id=me.user_id)
        status.note_me(me.username, me.user_id)
        log.info('Бот: %s (id %s)', me.username, me.user_id)
        # Однажды Люся подобрала собственное сообщение и записала себя
        # в сотрудники. Записи о себе в списке людей быть не должно
        if db.get_user(me.user_id):
            db.delete_user(me.user_id)
            log.info('Убрала запись о себе из списка людей')
        if not me.username:
            log.warning('У бота нет username — кнопка мини-приложения показана не будет')
    except Exception as e:
        status.note_me(None, None, error=e)
        log.exception('Не удалось получить данные бота')

    await register_commands(bot)

    mode = os.environ.get('BOT_MODE', 'polling').lower()
    watch_updates(bot)
    asyncio.create_task(reminder_loop(bot))       # напоминания о сроках
    asyncio.create_task(heartbeat())              # видно, что опрос жив
    asyncio.create_task(asked_loop(bot))          # «напомни завтра в 9…»
    asyncio.create_task(backup.backup_loop(bot))  # ночная резервная копия
    asyncio.create_task(razbor.razbor_loop(bot))  # вечерний разбор ленты по домам

    # Мини-приложение и вебхук живут на одном порту: Railway даёт один, а
    # выбирать между страницей записи и обновлениями не хочется
    from .webapp import hook_url, start as start_webapp
    port = int(os.environ.get('PORT') or 8080)
    hook = None
    if mode == 'webhook':
        from maxapi.webhook.aiohttp import AiohttpMaxWebhook
        hook = AiohttpMaxWebhook(dp=dp, bot=bot, secret=webhook_secret())
    try:
        await start_webapp(port, bot=bot, webhook=hook)
    except Exception:
        log.exception('Не удалось поднять сервер — бот работает без него')
        hook = None

    if mode == 'webhook' and hook is not None:
        url = os.environ.get('WEBHOOK_URL') or hook_url()
        podpisan = False
        if not url:
            log.error('Не знаю своего адреса — вебхук не зарегистрировать. '
                      'Задайте WEBHOOK_URL или RAILWAY_PUBLIC_DOMAIN')
        else:
            podpisan = await podpisatsya(bot, url)
        if podpisan:
            asyncio.create_task(storozh_podpiski(bot, url))
            # Обновления приходят в тот же сервер; здесь просто не выходим
            log.info('Запуск в режиме webhook')
            await asyncio.Event().wait()
            return
        # Подписаться не вышло — молчащий бот хуже неудобного режима
        log.warning('Вебхук не поднялся — возвращаюсь на long polling')
        mode = 'polling'

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
