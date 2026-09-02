"""Страница записи голоса — в обход MAX.

MAX не отдаёт ботам голосовые сообщения в личке: ни в уведомлении, ни по
прямому запросу. Это ограничение платформы, кодом оно не обходится.

Поэтому голос идёт мимо MAX. Бот и так поднимает свой веб-сервер для
мини-приложения — здесь к нему добавляется страница с одной кнопкой:
нажал, наговорил, отпустил. Запись уходит на сервер бота, расшифровывается
той же моделью и обрабатывается ровно как обычное сообщение, со всеми
привычными путями — стояки, опись, находки, объявления.

Адрес личный и он же пропуск: у каждого своя ссылка. Страницу можно
положить ярлыком на домашний экран — тогда до кнопки записи одно касание,
и MAX в этом не участвует вовсе.
"""
import logging
import os
import tempfile

from . import db, transcribe

log = logging.getLogger('golos')

MAX_BYTES = 12 * 1024 * 1024      # больше десяти минут речи нам не нужно

STRANITSA = """<!doctype html>
<html lang="ru"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-title" content="Люся">
<meta name="theme-color" content="#12161c">
<title>Люся — голос</title>
<style>
  :root{color-scheme:dark}
  *{box-sizing:border-box}
  body{margin:0;min-height:100vh;background:#12161c;color:#e9edf3;
       font:16px/1.5 -apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;
       display:flex;flex-direction:column;align-items:center;
       padding:24px 18px calc(24px + env(safe-area-inset-bottom))}
  h1{font-size:19px;font-weight:600;margin:4px 0 2px}
  .kto{color:#8a94a6;font-size:14px;margin-bottom:26px}
  #knopka{width:210px;height:210px;border-radius:50%;border:none;
          background:#2f6df6;color:#fff;font-size:20px;font-weight:600;
          box-shadow:0 10px 40px rgba(47,109,246,.35);
          transition:transform .12s,background .2s;
          -webkit-tap-highlight-color:transparent;touch-action:manipulation}
  #knopka:active{transform:scale(.97)}
  #knopka.pishet{background:#e5484d;box-shadow:0 10px 46px rgba(229,72,77,.45);
                 animation:puls 1.4s ease-in-out infinite}
  #knopka.zhdet{background:#4b5566;box-shadow:none}
  @keyframes puls{0%,100%{transform:scale(1)}50%{transform:scale(1.04)}}
  #tikaet{margin-top:16px;font-variant-numeric:tabular-nums;color:#8a94a6;
          font-size:15px;min-height:22px}
  #lenta{width:100%;max-width:520px;margin-top:26px;display:flex;
         flex-direction:column;gap:12px}
  .karta{background:#1b212b;border:1px solid #29313d;border-radius:14px;
         padding:14px 16px;white-space:pre-wrap;word-wrap:break-word}
  .karta .zagolovok{color:#8a94a6;font-size:12.5px;letter-spacing:.04em;
                    text-transform:uppercase;margin-bottom:6px}
  .oshibka{border-color:#5c2a2c;background:#2a1a1c}
  .podskazka{color:#69738a;font-size:13.5px;margin-top:22px;text-align:center;
             max-width:420px}
</style>
</head><body>
  <h1>Люся слушает</h1>
  <div class="kto">__KTO__</div>
  <button id="knopka">Говорить</button>
  <div id="tikaet"></div>
  <div id="lenta"></div>
  <div class="podskazka">Нажмите, наговорите и нажмите ещё раз.
    Ответ придёт сюда и в MAX.</div>
<script>
const knopka = document.getElementById('knopka');
const tikaet = document.getElementById('tikaet');
const lenta = document.getElementById('lenta');
let rec = null, kuski = [], pishet = false, nachalo = 0, chasy = null;

function karta(zagolovok, text, klass) {
  const d = document.createElement('div');
  d.className = 'karta' + (klass ? ' ' + klass : '');
  d.innerHTML = '<div class="zagolovok"></div><div class="telo"></div>';
  d.querySelector('.zagolovok').textContent = zagolovok;
  d.querySelector('.telo').textContent = text;
  lenta.prepend(d);
  return d;
}

function tik() {
  const s = Math.floor((Date.now() - nachalo) / 1000);
  tikaet.textContent = String(Math.floor(s / 60)).padStart(2, '0') + ':' +
                       String(s % 60).padStart(2, '0');
}

async function nachat() {
  let potok;
  try {
    potok = await navigator.mediaDevices.getUserMedia({audio: true});
  } catch (e) {
    karta('Не вышло', 'Микрофон недоступен: ' + e.message +
          '\\nРазрешите доступ к микрофону для этой страницы.', 'oshibka');
    return;
  }
  kuski = [];
  rec = new MediaRecorder(potok);
  rec.ondataavailable = e => { if (e.data.size) kuski.push(e.data); };
  rec.onstop = () => { potok.getTracks().forEach(t => t.stop()); otpravit(); };
  rec.start();
  pishet = true;
  nachalo = Date.now();
  knopka.textContent = 'Стоп';
  knopka.className = 'pishet';
  tik();
  chasy = setInterval(tik, 250);
}

function ostanovit() {
  pishet = false;
  clearInterval(chasy);
  knopka.textContent = 'Слушаю…';
  knopka.className = 'zhdet';
  knopka.disabled = true;
  if (rec && rec.state !== 'inactive') rec.stop();
}

async function otpravit() {
  const blob = new Blob(kuski, {type: rec.mimeType || 'audio/webm'});
  tikaet.textContent = 'Расшифровываю…';
  try {
    const otvet = await fetch('golos', {method: 'POST', body: blob,
                    headers: {'Content-Type': blob.type || 'audio/webm'}});
    const data = await otvet.json();
    if (data.error) {
      karta('Не вышло', data.error, 'oshibka');
    } else {
      if (data.text) karta('Услышала', data.text);
      if (data.reply) karta('Люся', data.reply);
    }
  } catch (e) {
    karta('Не вышло', 'Связь оборвалась: ' + e.message, 'oshibka');
  }
  tikaet.textContent = '';
  knopka.textContent = 'Говорить';
  knopka.className = '';
  knopka.disabled = false;
}

knopka.addEventListener('click', () => { pishet ? ostanovit() : nachat(); });
</script>
</body></html>"""


def stranitsa(imya: str) -> str:
    return STRANITSA.replace('__KTO__', imya or 'сотрудник')


async def rasshifrovat(data: bytes, mime: str) -> str | None:
    """Байты записи → текст. None — если распознать не вышло."""
    rasshirenie = '.webm'
    if 'mp4' in mime or 'aac' in mime:
        rasshirenie = '.mp4'
    elif 'ogg' in mime:
        rasshirenie = '.ogg'
    elif 'wav' in mime:
        rasshirenie = '.wav'
    papka = tempfile.mkdtemp(prefix='golos_')
    syroy = os.path.join(papka, 'zapis' + rasshirenie)
    try:
        with open(syroy, 'wb') as f:
            f.write(data)
        # Браузеры пишут в webm/ogg, модель ждёт mp3 — ffmpeg уже есть
        mp3 = await transcribe.extract_audio(syroy)
        if not mp3:
            return None
        try:
            return await transcribe.transcribe_file(mp3)
        finally:
            if os.path.exists(mp3):
                os.unlink(mp3)
    finally:
        try:
            if os.path.exists(syroy):
                os.unlink(syroy)
            os.rmdir(papka)
        except OSError:
            pass


def ssylka(user_id) -> str | None:
    """Личный адрес страницы записи для этого человека."""
    from .webapp import miniapp_path, public_url

    base = public_url()
    if not base:
        return None
    return f"{base.rstrip('/')}/golos/{db.issue_token(user_id)}/"


# ---------- Обработка сказанного ----------

class _Otvety:
    """Сообщение-заглушка: ловит ответы Люси и заодно шлёт их в MAX."""

    def __init__(self, user_id, imya, bot):
        import types

        self.user_id = user_id
        self.bot = bot
        self.sobrano = []
        self.body = types.SimpleNamespace(text='', attachments=None,
                                          mid=f'golos-{user_id}', markup=None)
        self.sender = types.SimpleNamespace(user_id=user_id, full_name=imya)
        self.recipient = types.SimpleNamespace(user_id=user_id, chat_id=None,
                                               chat_type='dialog')
        self.link = None

    async def answer(self, text=None, attachments=None):
        if text:
            self.sobrano.append(text)
        # В MAX ответ уходит тоже: там останутся кнопки и история переписки
        if self.bot is not None:
            try:
                await self.bot.send_message(user_id=self.user_id, text=text or '',
                                            attachments=attachments)
            except Exception:
                log.warning('Не удалось продублировать ответ в MAX', exc_info=True)


async def obrabotat(user_id, text: str, bot) -> str:
    """Прогоняет сказанное обычным путём и возвращает ответ Люси."""
    import types

    from . import handlers

    polzovatel = db.get_user(user_id)
    imya = (polzovatel['name'] if polzovatel else None) or 'сотрудник'
    soobschenie = _Otvety(user_id, imya, bot)
    soobschenie.body.text = text
    event = types.SimpleNamespace(message=soobschenie, bot=bot)
    event.callback = types.SimpleNamespace(
        user=types.SimpleNamespace(user_id=user_id, full_name=imya))
    try:
        await handlers.on_text(event)
    except Exception:
        log.exception('Не удалось обработать сказанное со страницы')
        return 'Записала, но обработать не смогла — посмотрю, в чём дело.'
    return '\n\n'.join(soobschenie.sobrano)
