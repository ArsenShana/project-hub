"""ProjectHub bot. Python 3.10+, no external dependencies."""
import json, os, re, time, urllib.request, urllib.error
TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN', '')
APP_URL = os.environ.get('MINI_APP_URL', '')
ADMIN = os.environ.get('ADMIN_CHAT_ID', '')

def api(method, **data):
    request = urllib.request.Request('https://api.telegram.org/bot' + TOKEN + '/' + method,
                                     data=json.dumps(data).encode(), headers={'Content-Type':'application/json'})
    with urllib.request.urlopen(request, timeout=40) as response:
        result = json.load(response)
    if not result.get('ok'):
        raise RuntimeError('Telegram rejected request')
    return result['result']

def handle(message):
    chat = message['chat']
    if chat['type'] != 'private':
        return
    chat_id = chat['id']
    if message.get('text', '').split('@')[0] in ('/start', '/help'):
        api('sendMessage', chat_id=chat_id, text='Добро пожаловать в ProjectHub!\nВыберите IT-проект или оставьте заявку в приложении.',
            reply_markup={'keyboard':[[{'text':'🚀 Открыть ProjectHub', 'web_app':{'url':APP_URL}}]], 'resize_keyboard':True})
    if 'web_app_data' in message:
        try:
            data = json.loads(message['web_app_data']['data'])
            fields = ('name', 'contact', 'project', 'description')
            if not isinstance(data, dict) or data.get('type') != 'request':
                raise ValueError()
            if any(not isinstance(data.get(k), str) or len(data[k]) > 1500 for k in fields):
                raise ValueError()
            if any(not data[k].strip() for k in ('name', 'contact', 'description')):
                raise ValueError()
        except (ValueError, TypeError):
            api('sendMessage', chat_id=chat_id, text='Не удалось прочитать заявку. Заполните форму заново.')
            return
        text = '\n'.join(['Новая заявка ProjectHub', 'Имя: '+data['name'], 'Контакт: '+data['contact'],
                         'Проект: '+data['project'], 'Задача: '+data['description']])
        if ADMIN:
            api('sendMessage', chat_id=ADMIN, text=text[:4000])
            api('sendMessage', chat_id=chat_id, text='Заявка отправлена менеджеру. Спасибо!')
        else:
            api('sendMessage', chat_id=chat_id, text='Заявка получена ботом. Менеджер пока не подключён.\n\n'+text[:3500])

def main():
    if not re.fullmatch(r'\d+:[A-Za-z0-9_-]{20,}', TOKEN):
        raise SystemExit('Set TELEGRAM_BOT_TOKEN to the full token issued by @BotFather.')
    if not APP_URL.startswith('https://'):
        raise SystemExit('Set MINI_APP_URL to a publicly reachable HTTPS Mini App URL.')
    api('getMe')
    offset = None
    print('ProjectHub bot started. Open /start in Telegram.')
    while True:
        try:
            updates = api('getUpdates', offset=offset, timeout=25, allowed_updates=['message'])
            for update in updates:
                if 'message' in update:
                    handle(update['message'])
                offset = update['update_id'] + 1
        except (urllib.error.URLError, RuntimeError):
            print('Telegram request failed; retrying in 5 seconds.')
            time.sleep(5)

if __name__ == '__main__':
    main()
