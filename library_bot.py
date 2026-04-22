#!/usr/bin/env python3
import argparse
import io
import json
import logging
import os
import re
import shlex
import smtplib
import subprocess
import tempfile
import time
import zipfile
import secrets
import string
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo
from email.message import EmailMessage
from pathlib import Path
from typing import Dict, List, Tuple

import requests

from runtime_store import RuntimeStore
from telegram_ui import books_human_list, is_valid_email, normalize_email, pagination_keyboard, render_search_page
from delivery_service import DeliveryService

logger = logging.getLogger(__name__)


@dataclass
class Book:
    book_id: str
    title: str
    authors: str
    archive_base: str
    file_base: str
    ext: str


def configure_logging() -> None:
    level_name = os.environ.get('LIBRARY_BOT_LOG_LEVEL', 'INFO').upper()
    level = getattr(logging, level_name, logging.INFO)
    logging.basicConfig(level=level, format='%(asctime)s %(levelname)s [%(name)s] %(message)s')


class LibraryBotCore:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.runtime = Path(cfg.get('work_dir', '/root/.openclaw/workspace/book/runtime'))
        self.runtime.mkdir(parents=True, exist_ok=True)
        self.index_path = self.runtime / 'books_index.jsonl'
        self.inpx_cache_path = self.runtime / 'flibusta_fb2_local.inpx'
        self.store = RuntimeStore(self.runtime)
        self.users_path = self.runtime / 'users.json'
        self.sent_log_path = self.runtime / 'book_send_log.txt'
        self.sent_index_path = self.runtime / 'sent_books_by_tg.json'
        self.smtp_path = Path(cfg['smtp_creds_path'])
        self.max_email_bytes = int(cfg.get('max_email_bytes', 15 * 1024 * 1024))
        self.delivery = DeliveryService(self.smtp_path, self.max_email_bytes)
        self.search_page_size = int(cfg.get('search_page_size', 3))

    def _load_inpx_meta(self) -> dict:
        return self.store.load_inpx_meta()

    def ensure_local_inpx_fresh(self, force: bool = False) -> bool:
        """Returns True if local INPX was updated."""
        inpx_name = self.cfg.get('inpx_name', 'flibusta_fb2_local.inpx')
        inpx_path = Path(inpx_name)
        if not inpx_path.is_absolute():
            inpx_path = Path.cwd() / inpx_path
        if not inpx_path.exists():
            raise RuntimeError(f'Local INPX file not found: {inpx_path}')

        src_stat = inpx_path.stat()
        local_meta = self._load_inpx_meta()
        need_copy = (
            force
            or (not self.inpx_cache_path.exists())
            or str(src_stat.st_mtime_ns) != str(local_meta.get('mtime_ns'))
            or str(src_stat.st_size) != str(local_meta.get('content_length'))
        )
        if need_copy:
            self.inpx_cache_path.write_bytes(inpx_path.read_bytes())
            self.store.save_inpx_meta({
                'source': str(inpx_path),
                'mtime_ns': str(src_stat.st_mtime_ns),
                'content_length': str(src_stat.st_size),
                'updated_at': int(time.time()),
            })
            return True
        return False

    def _load_smtp(self) -> dict:
        return self.delivery.load_smtp()

    def build_index(self) -> int:
        self.ensure_local_inpx_fresh(force=False)
        if not self.inpx_cache_path.exists():
            raise RuntimeError(f'Local INPX cache not found: {self.inpx_cache_path}')

        with zipfile.ZipFile(self.inpx_cache_path, 'r') as zf:
            structure = None
            if 'structure.info' in zf.namelist():
                structure = zf.read('structure.info').decode('utf-8', errors='ignore').strip()
                structure = [s.strip().lower() for s in re.split(r'[;|,]', structure) if s.strip()]

            count = 0
            with self.index_path.open('w', encoding='utf-8') as f:
                for name in zf.namelist():
                    if not name.lower().endswith('.inp'):
                        continue
                    archive_base = Path(name).stem
                    text = zf.read(name).decode('utf-8', errors='ignore')
                    for line in text.splitlines():
                        line = line.strip()
                        if not line:
                            continue
                        parts = line.split('\x04')
                        title, authors, file_base, ext, lang = self._extract_fields(parts, structure)
                        if not file_base:
                            continue
                        book = {
                            'book_id': f'{archive_base}:{file_base}',
                            'title': title or '',
                            'authors': authors or '',
                            'archive_base': archive_base,
                            'file_base': file_base,
                            'ext': (ext or 'fb2').lower(),
                            'lang': (lang or '').lower(),
                        }
                        f.write(json.dumps(book, ensure_ascii=False) + '\n')
                        count += 1
        return count

    @staticmethod
    def _extract_fields(parts: List[str], structure: List[str] | None) -> Tuple[str, str, str, str, str]:
        title = parts[2] if len(parts) > 2 else ''
        authors = parts[0] if len(parts) > 0 else ''
        file_base = parts[5] if len(parts) > 5 else (parts[0] if parts else '')
        ext = parts[8] if len(parts) > 8 else 'fb2'
        lang = parts[11] if len(parts) > 11 else ''

        if structure:
            field = {structure[i]: parts[i] for i in range(min(len(structure), len(parts)))}
            title = field.get('title', title)
            authors = field.get('author', field.get('authors', authors))
            file_base = field.get('file', field.get('filename', file_base))
            ext = field.get('ext', ext)
            lang = field.get('lang', field.get('language', lang))
        return title, authors, file_base, ext, lang

    def ensure_index_current_for_send(self) -> None:
        updated = self.ensure_local_inpx_fresh(force=False)
        if updated or (not self.index_path.exists()):
            self.build_index()

    def _iter_books(self):
        if not self.index_path.exists():
            raise RuntimeError('Index not found. Run index first.')
        with self.index_path.open('r', encoding='utf-8') as f:
            for line in f:
                if line.strip():
                    yield json.loads(line)

    @staticmethod
    def _normalize_authors(authors: str) -> str:
        text = (authors or '').strip().strip(':').strip()
        parts = [p.strip() for p in text.split(':') if p.strip()]
        if parts:
            text = parts[0]
        chunks = [c.strip() for c in text.split(',') if c.strip()]
        if len(chunks) >= 2:
            return ' '.join(chunks)
        return text or 'Автор не указан'

    @staticmethod
    def _looks_russian_by_text(text: str) -> bool:
        if not text:
            return False
        cyr = len(re.findall(r'[А-Яа-яЁё]', text))
        lat = len(re.findall(r'[A-Za-z]', text))
        if cyr == 0:
            return False
        return cyr >= (lat * 2)

    def _is_russian_book(self, b: dict) -> bool:
        lang = (b.get('lang') or '').lower().strip()
        if lang:
            if lang.startswith('ru') or 'рус' in lang:
                return True
            if lang.startswith(('en', 'de', 'fr', 'es', 'it', 'pl', 'tr', 'uk')):
                return False
        txt = f"{b.get('authors','')} {b.get('title','')}"
        return self._looks_russian_by_text(txt)

    def search(self, query: str, limit: int = 20) -> List[dict]:
        self.ensure_index_current_for_send()
        tokens = [t.lower() for t in re.split(r'\s+', query.strip()) if t.strip()]
        language = str(self.cfg.get('language', 'ru')).lower().strip()
        out = []
        for b in self._iter_books():
            if language == 'ru' and not self._is_russian_book(b):
                continue
            hay = f"{b.get('authors','')} {b.get('title','')}".lower()
            if all(t in hay for t in tokens):
                result = dict(b)
                result['authors'] = self._normalize_authors(result.get('authors', ''))
                result['download_epub'] = f"https://flibusta.is/b/{b['file_base']}/epub"
                result['book_id'] = b.get('file_base') or b.get('book_id')
                out.append(result)
                if len(out) >= limit:
                    break
        return out

    def fetch_book_epub(self, book: dict, out_dir: Path) -> Path:
        return self.delivery.fetch_book_epub(book, out_dir)

    @staticmethod
    def _safe_book_filename(authors: str, title: str, fallback: str) -> str:
        return DeliveryService.safe_book_filename(authors, title, fallback)

    def convert_fb2_to_epub(self, fb2_path: Path, title: str = '', authors: str = '', fallback: str = 'book') -> Path:
        return self.delivery.convert_fb2_to_epub(fb2_path, title=title, authors=authors, fallback=fallback)

    def send_epubs_by_email(self, tg_id: str, epub_paths: List[Path], to_email_override: str | None = None) -> tuple[int, str]:
        return self.delivery.send_epubs_by_email(tg_id, epub_paths, self.users_path, to_email_override=to_email_override)

    @staticmethod
    def _merge_user_pref(existing: dict | None, updates: dict | None) -> dict:
        result = dict(existing or {})
        for key, value in (updates or {}).items():
            if value is not None:
                result[key] = value
        return result

    def _store_user_email(self, tg_id: str, email: str, current_pref: dict | None = None) -> None:
        prefs = self._load_user_prefs()
        prefs[tg_id] = {
            'email': email,
            'book_format': (current_pref or {}).get('book_format', 'epub'),
        }
        self._save_user_prefs(prefs)

    @staticmethod
    def _parse_send_indexes(text: str) -> List[int]:
        return [int(x.strip()) for x in text.split(',') if x.strip().isdigit()]

    def _select_books_by_indexes(self, tg_id: str, idxs: List[int]) -> List[dict]:
        last = self._load_last_results().get(tg_id, [])
        return [last[i - 1] for i in idxs if 1 <= i <= len(last)]

    def _mark_books_sent(self, tg_id: str, selected: List[dict]) -> None:
        sent_idx = self._load_sent_index()
        sent_for_user = sent_idx.get(tg_id, {})
        now_ts = int(time.time())
        for b in selected:
            sent_for_user[b.get('book_id')] = now_ts
        sent_idx[tg_id] = sent_for_user
        self._save_sent_index(sent_idx)

    def _deliver_books(self, tg_id: str, selected: List[dict], to_email: str | None, duplicate: bool = False) -> tuple[int, str]:
        self.ensure_index_current_for_send()
        with tempfile.TemporaryDirectory(prefix='librarybot_') as td:
            td_path = Path(td)
            epubs = [self.fetch_book_epub(b, td_path) for b in selected]
            parts, target_email = self.send_epubs_by_email(tg_id, epubs, to_email_override=to_email)
        self._mark_books_sent(tg_id, selected)
        self._append_send_log(tg_id, target_email, selected, parts, duplicate=duplicate)
        return parts, target_email

    @staticmethod
    def _books_human_list(books: List[dict]) -> str:
        return books_human_list(books)

    @staticmethod
    def _normalize_email(value: str) -> str:
        return normalize_email(value)

    @staticmethod
    def _is_valid_email(value: str) -> bool:
        return is_valid_email(value)

    def _render_search_page(self, found: List[dict], page: int) -> str:
        return render_search_page(found, page, self.search_page_size)

    def _pagination_keyboard(self, page: int, total_items: int) -> dict | None:
        return pagination_keyboard(page, total_items, self.search_page_size)

    def _load_last_results(self) -> Dict[str, List[dict]]:
        return self.store.load_last_results()

    def _save_last_results(self, data: Dict[str, List[dict]]) -> None:
        self.store.save_last_results(data)

    def _load_dialog_state(self) -> Dict[str, dict]:
        return self.store.load_dialog_state()

    def _save_dialog_state(self, data: Dict[str, dict]) -> None:
        self.store.save_dialog_state(data)

    def _load_auth_state(self) -> Dict[str, dict]:
        state = self.store.load_auth_state()
        if state:
            return state

        if self.users_path.exists():
            try:
                users = json.loads(self.users_path.read_text(encoding='utf-8'))
                out = {}
                for tg_id, info in users.items():
                    if not isinstance(info, dict):
                        continue
                    entry = {}
                    if 'authorized' in info:
                        entry['ok'] = bool(info.get('authorized'))
                    if 'authorized_at' in info:
                        entry['authorized_at'] = info.get('authorized_at')
                    if 'otp' in info:
                        entry['otp'] = info.get('otp')
                    if 'otp_expires_at' in info:
                        entry['expires_at'] = info.get('otp_expires_at')
                    if entry:
                        out[tg_id] = entry
                return out
            except Exception:
                logger.exception("Failed to load auth state from users.json")
        return {}

    def _save_auth_state(self, data: Dict[str, dict]) -> None:
        self.store.save_auth_state(data)

        users = {}
        if self.users_path.exists():
            try:
                users = json.loads(self.users_path.read_text(encoding='utf-8'))
            except Exception:
                logger.exception("Failed to load users.json for email delivery")
                users = {}
        for tg_id, info in data.items():
            users.setdefault(tg_id, {})
            if isinstance(info, dict):
                users[tg_id]['authorized'] = bool(info.get('ok', False))
                if 'authorized_at' in info:
                    users[tg_id]['authorized_at'] = info.get('authorized_at')
                if info.get('otp'):
                    users[tg_id]['otp'] = info.get('otp')
                elif 'otp' in users[tg_id]:
                    users[tg_id].pop('otp', None)
                if info.get('expires_at'):
                    users[tg_id]['otp_expires_at'] = info.get('expires_at')
                elif 'otp_expires_at' in users[tg_id]:
                    users[tg_id].pop('otp_expires_at', None)
        RuntimeStore.write_json_atomic(self.users_path, users)

    def _notify_owner_otp(self, tg_id: str, otp: str) -> None:
        owner = str(self.cfg.get('otp_notify_chat_id', '427611'))
        txt = f"OTP для library bot: user={tg_id}, code={otp}, ttl=10m"
        subprocess.run([
            'openclaw', 'message', 'send',
            '--channel', 'telegram',
            '--target', owner,
            '--message', txt,
        ], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def _load_user_prefs(self) -> Dict[str, dict]:
        prefs = self.store.load_user_prefs()
        if prefs:
            return prefs

        if self.users_path.exists():
            try:
                users = json.loads(self.users_path.read_text(encoding='utf-8'))
                result = {}
                for k, v in users.items():
                    if not isinstance(v, dict):
                        continue
                    pref = {}
                    if v.get('email'):
                        pref['email'] = v.get('email')
                    if v.get('book_format'):
                        pref['book_format'] = v.get('book_format')
                    if pref:
                        result[k] = pref
                return result
            except Exception:
                logger.exception("Failed to load auth state from users.json")
        return {}

    def _save_user_prefs(self, data: Dict[str, dict]) -> None:
        self.store.save_user_prefs(data)

        users = {}
        if self.users_path.exists():
            try:
                users = json.loads(self.users_path.read_text(encoding='utf-8'))
            except Exception:
                logger.exception("Failed to load users.json for email delivery")
                users = {}
        for tg_id, pref in data.items():
            users[tg_id] = self._merge_user_pref(users.get(tg_id), pref if isinstance(pref, dict) else {})
        RuntimeStore.write_json_atomic(self.users_path, users)

    def _load_sent_index(self) -> Dict[str, dict]:
        return self.store.load_sent_index()

    def _save_sent_index(self, data: Dict[str, dict]) -> None:
        self.store.save_sent_index(data)

    def _append_send_log(self, tg_id: str, to_email: str, books: List[dict], parts: int, duplicate: bool = False) -> None:
        ts = datetime.now(ZoneInfo('Europe/Moscow')).strftime('%Y-%m-%d %H:%M:%S MSK')
        lines = [
            f'[{ts}] user={tg_id} email={to_email} parts={parts} duplicate={"yes" if duplicate else "no"}',
        ]
        for b in books:
            lines.append(f"  - {b.get('book_id','?')} | {b.get('title','')} | {b.get('authors','')}")
        lines.append('')
        with self.sent_log_path.open('a', encoding='utf-8') as f:
            f.write('\n'.join(lines))

    def run_telegram_bot(self):
        token = self.cfg['telegram']['bot_token']
        if not token or token == 'REPLACE_ME':
            raise RuntimeError('Set telegram.bot_token in config.json')
        base = f'https://api.telegram.org/bot{token}'
        offset = None

        def send(chat_id: int, text: str, reply_markup: dict | None = None):
            payload = {'chat_id': chat_id, 'text': text}
            if reply_markup:
                payload['reply_markup'] = reply_markup
            requests.post(f'{base}/sendMessage', json=payload, timeout=30)

        menu_kb = {
            'keyboard': [
                [{'text': '🔎 Поиск книги'}],
                [{'text': '❓ Помощь'}]
            ],
            'resize_keyboard': True,
            'is_persistent': True
        }

        def email_manage_kb() -> dict:
            return {
                'inline_keyboard': [[
                    {'text': 'Сменить email', 'callback_data': 'email:change'}
                ]]
            }

        def format_manage_kb() -> dict:
            return {
                'inline_keyboard': [
                    [
                        {'text': 'fb2', 'callback_data': 'format:set:fb2'},
                        {'text': 'fb2.zip', 'callback_data': 'format:set:fb2.zip'},
                        {'text': 'epub', 'callback_data': 'format:set:epub'}
                    ]
                ]
            }

        requests.post(f'{base}/setMyCommands', json={
            'commands': [
                {'command': 'start', 'description': 'Запуск и авторизация'},
                {'command': 'search', 'description': 'Поиск книги'},
                {'command': 'send', 'description': 'Отправить найденные книги'},
                {'command': 'email', 'description': 'Указать или сменить email'},
                {'command': 'format', 'description': 'Выбрать формат книг'},
                {'command': 'help', 'description': 'Справка'}
            ]
        }, timeout=30)

        while True:
            params = {'timeout': 30}
            if offset is not None:
                params['offset'] = offset
            r = requests.get(f'{base}/getUpdates', params=params, timeout=40)
            data = r.json()
            for upd in data.get('result', []):
                offset = upd['update_id'] + 1

                # inline pagination callbacks
                cq = upd.get('callback_query')
                if cq:
                    try:
                        cq_id = cq.get('id')
                        tg_id = str(cq.get('from', {}).get('id', ''))
                        data_cb = (cq.get('data') or '').strip()
                        msg_cb = cq.get('message', {})
                        chat_id_cb = msg_cb.get('chat', {}).get('id')
                        message_id_cb = msg_cb.get('message_id')

                        if data_cb.startswith('pg:') and chat_id_cb and message_id_cb:
                            page = int(data_cb.split(':', 1)[1])
                            found = self._load_last_results().get(tg_id, [])
                            text_new = self._render_search_page(found, page)
                            kb = self._pagination_keyboard(page, len(found))
                            payload = {
                                'chat_id': chat_id_cb,
                                'message_id': message_id_cb,
                                'text': text_new,
                            }
                            if kb:
                                payload['reply_markup'] = kb
                            requests.post(f'{base}/editMessageText', json=payload, timeout=30)

                        elif data_cb == 'email:change' and chat_id_cb:
                            state = self._load_dialog_state()
                            state[tg_id] = {'await': 'email_input'}
                            self._save_dialog_state(state)
                            send(chat_id_cb, 'Введите новый email', reply_markup=menu_kb)
                        elif data_cb.startswith('format:set:') and chat_id_cb:
                            fmt = data_cb.split(':', 2)[2]
                            if fmt in ('fb2', 'fb2.zip', 'epub'):
                                prefs = self._load_user_prefs()
                                prefs.setdefault(tg_id, {})
                                prefs[tg_id]['book_format'] = fmt
                                self._save_user_prefs(prefs)
                                state = self._load_dialog_state()
                                if state.get(tg_id, {}).get('await') == 'format_input':
                                    state[tg_id] = {}
                                    self._save_dialog_state(state)
                                send(chat_id_cb, f'✅ Формат книг сохранён: {fmt}', reply_markup=menu_kb)
                        if cq_id:
                            requests.post(f'{base}/answerCallbackQuery', json={'callback_query_id': cq_id}, timeout=30)
                    except Exception:
                        logger.exception("Callback handling failed")
                    continue

                msg = upd.get('message', {})
                text = (msg.get('text') or '').strip()
                if not text:
                    continue
                chat_id = msg['chat']['id']
                tg_id = str(msg['from']['id'])

                try:
                    state = self._load_dialog_state()
                    st = state.get(tg_id, {})
                    auth = self._load_auth_state()
                    a = auth.get(tg_id, {})
                    prefs = self._load_user_prefs()
                    pref = prefs.get(tg_id, {})
                    sent_idx = self._load_sent_index()
                    sent_for_user = sent_idx.get(tg_id, {})

                    # first-time authorization via one-time password
                    if not a.get('ok'):
                        entered_code = None
                        if text.startswith('/otp '):
                            entered_code = text.split(' ', 1)[1].strip()
                        elif re.fullmatch(r'\d{4,8}', text.strip()):
                            entered_code = text.strip()

                        if entered_code is not None:
                            if entered_code == a.get('otp') and time.time() <= float(a.get('expires_at', 0)):
                                auth[tg_id] = {'ok': True, 'authorized_at': int(time.time())}
                                self._save_auth_state(auth)
                                state[tg_id] = {'await': 'email_input'}
                                self._save_dialog_state(state)
                                send(chat_id, '✅ Авторизация успешна.\nСначала введите email для отправки', reply_markup=menu_kb)
                            else:
                                send(chat_id, '❌ Неверный или просроченный код. Запроси новый: /start', reply_markup=menu_kb)
                            continue

                        if text.startswith('/start') or not a.get('otp') or time.time() > float(a.get('expires_at', 0)):
                            otp = ''.join(secrets.choice(string.digits) for _ in range(6))
                            auth[tg_id] = {'ok': False, 'otp': otp, 'expires_at': int(time.time()) + 600}
                            self._save_auth_state(auth)
                            self._notify_owner_otp(tg_id, otp)

                        send(chat_id, 'Необходимо ввести код авторизации', reply_markup=menu_kb)
                        continue

                    # /email command when email already set
                    if text.strip() == '/email' and pref.get('email'):
                        requests.post(f'{base}/sendMessage', json={
                            'chat_id': chat_id,
                            'text': f"Текущий email: {pref.get('email')}",
                            'reply_markup': email_manage_kb(),
                        }, timeout=30)
                        continue

                    # /format command
                    if text.strip() in ('/format', '⚙️ Формат'):
                        current_fmt = pref.get('book_format', 'epub')
                        requests.post(f'{base}/sendMessage', json={
                            'chat_id': chat_id,
                            'text': f"Текущий формат книг: {current_fmt}\nВыберите новый формат:",
                            'reply_markup': format_manage_kb(),
                        }, timeout=30)
                        continue

                    # common email input flow (first setup or change)
                    if st.get('await') == 'email_input':
                        if pref.get('email') and not text.startswith('/email '):
                            state[tg_id] = {}
                            self._save_dialog_state(state)
                        else:
                            em = text.strip()
                            if text.startswith('/email '):
                                em = text.split(' ', 1)[1].strip()
                            em = self._normalize_email(em)
                            if self._is_valid_email(em):
                                self._store_user_email(tg_id, em, pref)
                                state[tg_id] = {'await': 'format_input'}
                                self._save_dialog_state(state)
                                send(chat_id, f'✅ Email сохранён: {em}\nТеперь выберите формат книг по умолчанию:', reply_markup=format_manage_kb())
                            else:
                                send(chat_id, '❌ Некорректный email. Введите email в формате user@kindle.com', reply_markup=menu_kb)
                            continue

                    # first-time email capture for delivery
                    if not pref.get('email'):
                        if text.startswith('/email '):
                            em = self._normalize_email(text.split(' ', 1)[1].strip())
                            if self._is_valid_email(em):
                                self._store_user_email(tg_id, em, pref)
                                state[tg_id] = {'await': 'format_input'}
                                self._save_dialog_state(state)
                                send(chat_id, f'✅ Email сохранён: {em}\nТеперь выберите формат книг по умолчанию:', reply_markup=format_manage_kb())
                            else:
                                send(chat_id, '❌ Некорректный email. Введите email в формате user@kindle.com', reply_markup=menu_kb)
                            continue

                        state[tg_id] = {'await': 'email_input'}
                        self._save_dialog_state(state)
                        send(chat_id, 'Сначала введите email для отправки', reply_markup=menu_kb)
                        continue

                    if st.get('await') == 'format_input':
                        fmt = text.strip().lower()
                        if fmt in ('fb2', 'fb2.zip', 'epub'):
                            prefs = self._load_user_prefs()
                            prefs.setdefault(tg_id, {})
                            prefs[tg_id]['book_format'] = fmt
                            self._save_user_prefs(prefs)
                            state[tg_id] = {}
                            self._save_dialog_state(state)
                            send(chat_id, f'✅ Формат книг сохранён: {fmt}', reply_markup=menu_kb)
                        else:
                            send(chat_id, 'Выберите формат: fb2, fb2.zip или epub', reply_markup=format_manage_kb())
                        continue

                    # allow plain number input after search: "1" or "1,2,3"
                    if re.fullmatch(r'\d+(\s*,\s*\d+)*', text.strip()) and not st.get('await'):
                        text = '/send ' + text.strip()

                    if text in ('🔎 Поиск книги', '/search'):
                        state[tg_id] = {'await': 'search_query'}
                        self._save_dialog_state(state)
                        send(chat_id, 'Введи запрос (автор/название/комбинация):', reply_markup=menu_kb)

                    elif text in ('📤 Отправить выбранные', '/send'):
                        state[tg_id] = {'await': 'send_indexes'}
                        self._save_dialog_state(state)
                        send(chat_id, 'Введи номера найденных книг через запятую, например: 1,2,3', reply_markup=menu_kb)

                    elif text.startswith('/help') or text.startswith('/menu') or text == '❓ Помощь' or text.lower() == 'помощь':
                        send(chat_id,
                             '📚 Library Bot — справка\n\n'
                             '1) Первый вход\n'
                             '• Нажми /start\n'
                             '• Получи OTP у владельца и введи: /otp 123456\n\n'
                             '2) Укажи email для отправки\n'
                             '• /email user@kindle.com\n'
                             '• Можно менять в любой момент той же командой\n\n'
                             '3) Выбери формат книг\n'
                             '• /format\n'
                             '• Доступно: fb2, fb2.zip, epub\n\n'
                             '4) Поиск книг\n'
                             '• /search <автор/название/комбинация>\n'
                             '• Пример: /search Пелевин Generation\n\n'
                             '5) Отправка\n'
                             '• /send 1,2,3 — номера из последнего поиска\n'
                             '• Если книга уже отправлялась, бот спросит подтверждение (ДА/НЕТ)\n\n'
                             '6) Команды\n'
                             '• /search — поиск книги\n'
                             '• /send 1,2,3 — отправка книг\n'
                             '• /format — выбор формата\n\n'
                             'Примечание: вложения режутся на несколько писем, если общий размер > 15 MB.',
                             reply_markup=menu_kb)

                    elif text.startswith('/search '):
                        q = text[len('/search '):].strip()
                        found = self.search(q, limit=60)
                        last = self._load_last_results()
                        last[tg_id] = found
                        self._save_last_results(last)
                        if not found:
                            send(chat_id, 'Ничего не найдено.', reply_markup=menu_kb)
                        else:
                            text_page = self._render_search_page(found, 1)
                            inline_kb = self._pagination_keyboard(1, len(found))
                            if inline_kb:
                                requests.post(f'{base}/sendMessage', json={
                                    'chat_id': chat_id,
                                    'text': text_page,
                                    'reply_markup': inline_kb,
                                }, timeout=30)
                            else:
                                send(chat_id, text_page, reply_markup=menu_kb)

                    elif text.startswith('/send '):
                        idxs = self._parse_send_indexes(text[len('/send '):])
                        last = self._load_last_results().get(tg_id, [])
                        if not last:
                            send(chat_id, 'Сначала сделай /search', reply_markup=menu_kb)
                            continue
                        selected = self._select_books_by_indexes(tg_id, idxs)
                        if not selected:
                            send(chat_id, 'Не выбраны валидные индексы.', reply_markup=menu_kb)
                            continue

                        dup = [b for b in selected if b.get('book_id') in sent_for_user]
                        if dup:
                            dup_titles = '; '.join((b.get('title') or b.get('book_id')) for b in dup[:3])
                            state[tg_id] = {
                                'await': 'confirm_resend',
                                'selected': selected,
                            }
                            self._save_dialog_state(state)
                            send(chat_id, f'⚠️ Эти книги уже отправлялись: {dup_titles}. Повторить отправку? Ответь: ДА или НЕТ', reply_markup=menu_kb)
                            continue

                        parts, target_email = self._deliver_books(tg_id, selected, pref.get('email'), duplicate=False)
                        sent_books = self._books_human_list(selected)
                        send(chat_id, f'{sent_books} отправлена(ы) на {target_email}', reply_markup=menu_kb)

                    elif st.get('await') == 'search_query':
                        q = text.strip()
                        found = self.search(q, limit=60)
                        last = self._load_last_results()
                        last[tg_id] = found
                        self._save_last_results(last)
                        state[tg_id] = {}
                        self._save_dialog_state(state)
                        if not found:
                            send(chat_id, 'Ничего не найдено.', reply_markup=menu_kb)
                        else:
                            text_page = self._render_search_page(found, 1)
                            inline_kb = self._pagination_keyboard(1, len(found))
                            if inline_kb:
                                requests.post(f'{base}/sendMessage', json={
                                    'chat_id': chat_id,
                                    'text': text_page,
                                    'reply_markup': inline_kb,
                                }, timeout=30)
                            else:
                                send(chat_id, text_page, reply_markup=menu_kb)

                    elif st.get('await') == 'send_indexes':
                        idxs = self._parse_send_indexes(text)
                        last = self._load_last_results().get(tg_id, [])
                        state[tg_id] = {}
                        self._save_dialog_state(state)
                        if not last:
                            send(chat_id, 'Сначала сделай /search', reply_markup=menu_kb)
                            continue
                        selected = self._select_books_by_indexes(tg_id, idxs)
                        if not selected:
                            send(chat_id, 'Не выбраны валидные индексы.', reply_markup=menu_kb)
                            continue

                        dup = [b for b in selected if b.get('book_id') in sent_for_user]
                        if dup:
                            dup_titles = '; '.join((b.get('title') or b.get('book_id')) for b in dup[:3])
                            state[tg_id] = {
                                'await': 'confirm_resend',
                                'selected': selected,
                            }
                            self._save_dialog_state(state)
                            send(chat_id, f'⚠️ Эти книги уже отправлялись: {dup_titles}. Повторить отправку? Ответь: ДА или НЕТ', reply_markup=menu_kb)
                            continue

                        parts, target_email = self._deliver_books(tg_id, selected, pref.get('email'), duplicate=False)
                        send(chat_id, f'Отправлено писем: {parts} на {target_email}', reply_markup=menu_kb)

                    elif st.get('await') == 'confirm_resend':
                        answer = text.strip().lower()
                        if answer in ('да', 'yes', 'y', 'ok'):
                            selected = st.get('selected') or []
                            if not selected:
                                state[tg_id] = {}
                                self._save_dialog_state(state)
                                send(chat_id, 'Не нашёл книги для повторной отправки. Сделай /search заново.', reply_markup=menu_kb)
                                continue

                            parts, target_email = self._deliver_books(tg_id, selected, pref.get('email'), duplicate=True)
                            state[tg_id] = {}
                            self._save_dialog_state(state)
                            sent_books = self._books_human_list(selected)
                            send(chat_id, f'{sent_books} отправлена(ы) на {target_email}', reply_markup=menu_kb)
                        elif answer in ('нет', 'no', 'n', 'cancel', 'отмена'):
                            state[tg_id] = {}
                            self._save_dialog_state(state)
                            send(chat_id, 'Ок, повторную отправку отменил.', reply_markup=menu_kb)
                        else:
                            send(chat_id, 'Ответь: ДА или НЕТ', reply_markup=menu_kb)

                    else:
                        send(chat_id, 'Нажми кнопку «🔎 Поиск книги» или /help', reply_markup=menu_kb)
                except Exception as e:
                    send(chat_id, f'Ошибка: {e}', reply_markup=menu_kb)


def load_cfg(path: Path) -> dict:
    return json.loads(path.read_text(encoding='utf-8'))


def cmd_index(core: LibraryBotCore, _args):
    n = core.build_index()
    print(f'INDEX_OK books={n} path={core.index_path}')


def cmd_search(core: LibraryBotCore, args):
    out = core.search(args.query, limit=args.limit)
    for i, b in enumerate(out, 1):
        print(f"{i}. {b.get('authors','')} — {b.get('title','')} [{b['book_id']}]")


def cmd_runbot(core: LibraryBotCore, _args):
    core.run_telegram_bot()


def main():
    configure_logging()
    p = argparse.ArgumentParser()
    p.add_argument('--config', required=True)
    sp = p.add_subparsers(dest='cmd', required=True)

    sp.add_parser('index')
    s = sp.add_parser('search')
    s.add_argument('--query', required=True)
    s.add_argument('--limit', type=int, default=20)
    sp.add_parser('runbot')

    args = p.parse_args()
    core = LibraryBotCore(load_cfg(Path(args.config)))

    if args.cmd == 'index':
        cmd_index(core, args)
    elif args.cmd == 'search':
        cmd_search(core, args)
    elif args.cmd == 'runbot':
        cmd_runbot(core, args)


if __name__ == '__main__':
    main()