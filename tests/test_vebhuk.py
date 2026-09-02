"""Вебхук живёт на том же сервере, что и мини-приложение.

В статье на Хабре про Bot API MAX сказано: голосовое приходит ссылкой —
но у автора вебхук, а у нас был long polling, где MAX присылает пустое
уведомление. Заодно оттуда же: подписка пропадает сама и её надо
переустанавливать, а /chats возвращает только групповые чаты.
"""
import pytest
from aiohttp.test_utils import TestClient, TestServer

from bot import db, main as bot_main, webapp


@pytest.fixture(autouse=True)
def baza(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DB_PATH', str(tmp_path / 'test.db'))
    db.init()
    webapp._cache.update(html=None, at=0.0)


def test_adres_vebhuka_sekretnyy(monkeypatch):
    monkeypatch.setenv('MINIAPP_PATH', 'tayna')
    monkeypatch.setenv('RAILWAY_PUBLIC_DOMAIN', 'map-irk.up.railway.app')

    assert webapp.hook_path() == '/tayna/hook'
    assert webapp.hook_url() == 'https://map-irk.up.railway.app/tayna/hook'


def test_bez_domena_adresa_net(monkeypatch):
    monkeypatch.delenv('RAILWAY_PUBLIC_DOMAIN', raising=False)
    monkeypatch.delenv('MINIAPP_HOST', raising=False)

    assert webapp.hook_url() is None


def test_sekret_beryotsya_iz_tokena(monkeypatch):
    """Лишняя настройка на боевом сервере — лишний повод её забыть."""
    monkeypatch.delenv('WEBHOOK_SECRET', raising=False)
    monkeypatch.setenv('MAX_BOT_TOKEN', 'токен-бота')

    secret = bot_main.webhook_secret()

    assert len(secret) == 32
    assert secret == bot_main.webhook_secret(), 'один и тот же при перезапуске'
    assert 'токен' not in secret, 'сам токен не светим'


def test_svoy_sekret_glavnee(monkeypatch):
    monkeypatch.setenv('WEBHOOK_SECRET', 'мой-секрет')

    assert bot_main.webhook_secret() == 'мой-секрет'


class FakeHook:
    """Заглушка вебхука: важно, что маршрут появляется на том же сервере."""

    def __init__(self):
        self.put = None

    async def on_startup(self, app):
        pass

    def setup(self, app, path='/'):
        from aiohttp import web

        async def _prinyat(request):
            return web.json_response({'ok': True})

        self.put = path
        app.router.add_post(path, _prinyat)


async def test_prilozhenie_i_vebhuk_na_odnom_portu(monkeypatch):
    """Railway даёт один порт — выбирать между ними не хочется."""
    monkeypatch.setenv('MINIAPP_PATH', 'tayna')
    hook = FakeHook()
    client = TestClient(TestServer(webapp.create_app(hook)))
    await client.start_server()

    assert hook.put == '/tayna/hook'
    otvet = await client.post('/tayna/hook', json={'update_type': 'x'})
    assert otvet.status == 200

    zdorovye = await client.get('/healthz')
    assert zdorovye.status == 200, 'приложение не потерялось'


async def test_bez_vebhuka_marshruta_net(monkeypatch):
    monkeypatch.setenv('MINIAPP_PATH', 'tayna')
    client = TestClient(TestServer(webapp.create_app()))
    await client.start_server()

    otvet = await client.post('/tayna/hook', json={})

    assert otvet.status == 404


# ---------- Подписка ----------

class FakeBot:
    def __init__(self, upadyot=False, podpiski=None):
        self.upadyot = upadyot
        self.snyato = 0
        self.podpisan = []
        self.podpiski = podpiski or []

    async def delete_webhook(self):
        self.snyato += 1

    async def subscribe_webhook(self, url=None, secret=None):
        if self.upadyot:
            raise RuntimeError('MAX недоступен')
        self.podpisan.append((url, secret))

    async def get_subscriptions(self):
        import types
        return types.SimpleNamespace(
            subscriptions=[types.SimpleNamespace(url=u) for u in self.podpiski])


async def test_pered_podpiskoy_snimaem_prezhnie():
    """MAX подписки копит, а не заменяет."""
    bot = FakeBot()

    ok = await bot_main.podpisatsya(bot, 'https://x/hook')

    assert ok is True
    assert bot.snyato == 1, 'старые сняты'
    assert bot.podpisan[0][0] == 'https://x/hook'
    assert bot.podpisan[0][1], 'секрет передан'


async def test_neudachnaya_podpiska_ne_vryot():
    bot = FakeBot(upadyot=True)

    assert await bot_main.podpisatsya(bot, 'https://x/hook') is False


async def test_storozh_vosstanavlivaet_propavshuyu_podpisku(monkeypatch):
    """Подписка пропадает сама, а тишина в чате выглядит как поломка."""
    bot = FakeBot(podpiski=[])
    shagi = []

    async def bystro(_):
        shagi.append(1)
        if len(shagi) > 1:
            raise SystemExit

    monkeypatch.setattr(bot_main.asyncio, 'sleep', bystro)

    with pytest.raises(SystemExit):
        await bot_main.storozh_podpiski(bot, 'https://x/hook')

    assert bot.podpisan, 'подписку восстановили'


async def test_zhivuyu_podpisku_ne_trogaem(monkeypatch):
    bot = FakeBot(podpiski=['https://x/hook'])
    shagi = []

    async def bystro(_):
        shagi.append(1)
        if len(shagi) > 1:
            raise SystemExit

    monkeypatch.setattr(bot_main.asyncio, 'sleep', bystro)

    with pytest.raises(SystemExit):
        await bot_main.storozh_podpiski(bot, 'https://x/hook')

    assert bot.podpisan == [], 'лишний раз не переподписываемся'
