"""Project Hub: Telegram-бот магазина проектов. Python 3.10+, без зависимостей.

Запуск сервера:        python3 bot.py
Регистрация webhook:   python3 bot.py --set-webhook
"""
import hashlib
import hmac
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import logging
import os
from pathlib import Path
import re
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, urlsplit
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent
WEBHOOK_PATH = '/telegram/webhook'
APP_PAGE = ROOT / 'app' / 'index.html'


def config():
    if (ROOT / '.env').exists():
        for line in (ROOT / '.env').read_text().splitlines():
            if '=' in line and not line.lstrip().startswith('#'):
                key, value = line.split('=', 1)
                os.environ.setdefault(key.strip(), value.strip())
    return os.environ


def load_projects():
    return json.loads((ROOT / 'projects.json').read_text())


def price_text(price):
    return f'{price:,}'.replace(',', ' ') + ' ₸'


class Bot:
    def __init__(self, token, domain, admin_id=''):
        self.token = token
        self.app_url = f'https://{domain}/app/'
        self.admin = int(admin_id) if admin_id else None

    def api(self, method, **payload):
        req = Request(f'https://api.telegram.org/bot{self.token}/{method}',
                      data=json.dumps(payload).encode(),
                      headers={'Content-Type': 'application/json'})
        try:
            with urlopen(req, timeout=40) as response:
                result = json.load(response)
        except HTTPError as exc:
            raise RuntimeError(f'Telegram: HTTP {exc.code}') from None
        except (URLError, TimeoutError):
            raise RuntimeError('Telegram: нет соединения') from None
        if not result.get('ok'):
            raise RuntimeError('Telegram отклонил запрос')
        return result['result']

    def send(self, chat, text, rows=None):
        payload = {'chat_id': chat, 'text': text}
        if rows:
            payload['reply_markup'] = {'inline_keyboard': rows}
        return self.api('sendMessage', **payload)

    def verify_init_data(self, init_data):
        """Проверяет подпись Telegram.WebApp.initData и возвращает пользователя."""
        fields = dict(parse_qsl(init_data, keep_blank_values=True))
        supplied = fields.pop('hash', '')
        check = '\n'.join(f'{k}={v}' for k, v in sorted(fields.items()))
        key = hmac.new(b'WebAppData', self.token.encode(), hashlib.sha256).digest()
        expected = hmac.new(key, check.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(supplied, expected):
            return None
        if time.time() - int(fields.get('auth_date', '0')) > 24 * 3600:
            return None
        return json.loads(fields.get('user', 'null'))

    def order(self, user, project):
        contact = '@' + user['username'] if user.get('username') else f"ID: {user['id']}"
        if self.admin:
            self.send(self.admin, f"🛒 Заявка: {project['title']}\nЦена: {price_text(project['price'])}\n"
                      f"Покупатель: {contact}\nИмя: {user.get('first_name', '')}", [[
                          {'text': 'Написать покупателю', 'url': f"tg://user?id={user['id']}"}]])
        else:
            logging.warning('ADMIN_ID не задан: заявка на проект %s никому не отправлена', project['id'])
        self.send(user['id'], f"✅ Заявка на «{project['title']}» принята.\n"
                  f"Цена: {price_text(project['price'])}\n"
                  'Оплата ещё не произведена — продавец свяжется с вами.')

    def handle(self, update):
        message = update.get('message') or {}
        chat = message.get('chat', {})
        if chat.get('type') != 'private':
            return
        command = message.get('text', '').split(' ')[0].split('@')[0]
        if command == '/id':
            return self.send(chat['id'], f"Ваш Telegram ID: {message['from']['id']}")
        if command == '/start':
            name = message.get('from', {}).get('first_name', '')
            self.send(chat['id'], f'Привет, {name}! 👋\n'
                                  'Это Project Hub — здесь можно купить готовые проекты.\n'
                                  'Откройте каталог, чтобы выбрать тему.', [[
                                      {'text': '📚 Открыть каталог', 'web_app': {'url': self.app_url}}]])


def handler(bot, secret):
    class Handler(BaseHTTPRequestHandler):
        def reply(self, code, data):
            body = json.dumps(data).encode()
            self.send_response(code)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def read_json(self):
            length = int(self.headers.get('Content-Length', '0'))
            if not 0 < length <= 1024 * 1024:
                raise ValueError('invalid size')
            data = json.loads(self.rfile.read(length))
            if not isinstance(data, dict):
                raise ValueError('invalid body')
            return data

        def do_GET(self):
            path = urlsplit(self.path).path
            if path == '/health':
                return self.reply(200, {'service': 'helpder', 'status': 'ok'})
            if path == '/api/projects':
                return self.reply(200, load_projects())
            if path == '/app/':
                body = APP_PAGE.read_bytes()
                self.send_response(200)
                self.send_header('Content-Type', 'text/html; charset=utf-8')
                self.send_header('Content-Length', str(len(body)))
                self.send_header('Cache-Control', 'no-cache')
                self.end_headers()
                return self.wfile.write(body)
            self.reply(404, {'error': 'not found'})

        def do_POST(self):
            if self.path == '/api/order':
                return self.post_order()
            if self.path != WEBHOOK_PATH:
                return self.reply(404, {'error': 'not found'})
            supplied = self.headers.get('X-Telegram-Bot-Api-Secret-Token', '')
            if not hmac.compare_digest(supplied.encode(), secret.encode()):
                return self.reply(403, {'error': 'forbidden'})
            try:
                update = self.read_json()
            except (ValueError, OSError):
                return self.reply(400, {'error': 'invalid body'})
            try:
                bot.handle(update)
            except Exception as exc:
                logging.error('Обработка webhook: %s', type(exc).__name__)
                return self.reply(503, {'error': 'retry later'})
            self.reply(200, {'ok': True})

        def post_order(self):
            try:
                data = self.read_json()
            except (ValueError, OSError):
                return self.reply(400, {'error': 'invalid body'})
            user = bot.verify_init_data(str(data.get('initData', '')))
            if not user:
                return self.reply(403, {'error': 'Откройте приложение из Telegram'})
            project = next((p for p in load_projects() if p['id'] == data.get('projectId')), None)
            if not project:
                return self.reply(404, {'error': 'Проект не найден'})
            try:
                bot.order(user, project)
            except RuntimeError:
                return self.reply(502, {'error': 'Не удалось отправить заявку, попробуйте позже'})
            self.reply(200, {'ok': True})

        def log_message(self, fmt, *args):
            # Не записываем запросы или секреты в журнал.
            pass

    return Handler


def main():
    settings = config()
    if not settings.get('BOT_TOKEN'):
        raise SystemExit('Укажите BOT_TOKEN в .env')
    secret = settings.get('WEBHOOK_SECRET', '')
    if not re.fullmatch(r'[A-Za-z0-9_-]{32,256}', secret):
        raise SystemExit('Укажите WEBHOOK_SECRET (32–256 символов A-Z, a-z, 0-9, _, -).')
    domain = settings.get('BOT_DOMAIN', 'bot.project-hub.asia')
    bot = Bot(settings['BOT_TOKEN'], domain, settings.get('ADMIN_ID', ''))
    if '--set-webhook' in sys.argv:
        url = f'https://{domain}{WEBHOOK_PATH}'
        bot.api('setWebhook', url=url, secret_token=secret,
                allowed_updates=['message', 'callback_query'], drop_pending_updates=True)
        bot.api('setChatMenuButton', menu_button={
            'type': 'web_app', 'text': 'Каталог', 'web_app': {'url': bot.app_url}})
        bot.api('setMyCommands', commands=[
            {'command': 'start', 'description': 'Главное меню'},
            {'command': 'id', 'description': 'Мой Telegram ID'}])
        return print('Webhook и кнопка приложения подключены: ' + url)
    address = ('127.0.0.1', int(settings.get('WEBHOOK_PORT', '8088')))
    with HTTPServer(address, handler(bot, secret)) as server:
        logging.info('Webhook слушает %s:%s', *address)
        server.serve_forever()


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(message)s')
    try:
        main()
    except KeyboardInterrupt:
        pass
    except RuntimeError as exc:
        raise SystemExit(str(exc)) from None
