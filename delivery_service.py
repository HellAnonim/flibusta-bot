from __future__ import annotations

import re
import smtplib
import subprocess
from email.message import EmailMessage
from pathlib import Path
from typing import List, Tuple

import requests


class DeliveryService:
    def __init__(self, smtp_path: Path, max_email_bytes: int):
        self.smtp_path = smtp_path
        self.max_email_bytes = max_email_bytes

    def load_smtp(self) -> dict:
        out = {}
        for line in self.smtp_path.read_text(encoding='utf-8').splitlines():
            line = line.strip()
            if not line or line.startswith('#') or '=' not in line:
                continue
            k, v = line.split('=', 1)
            out[k.strip()] = v.strip()
        return out

    @staticmethod
    def safe_book_filename(authors: str, title: str, fallback: str) -> str:
        base = f"{title} - {authors}".strip(' -')
        if not base:
            base = fallback
        base = re.sub(r'[\\/:*?"<>|]+', ' ', base)
        base = re.sub(r'\s+', ' ', base).strip()
        return base[:120] if base else fallback

    def fetch_book_epub(self, book: dict, out_dir: Path) -> Path:
        url = book.get('download_epub') or f"https://flibusta.is/b/{book['book_id']}/epub"
        r = requests.get(url, timeout=60)
        r.raise_for_status()
        out_dir.mkdir(parents=True, exist_ok=True)
        out_name = self.safe_book_filename(book.get('authors', ''), book.get('title', ''), str(book.get('book_id', 'book'))) + '.epub'
        epub_path = out_dir / out_name
        epub_path.write_bytes(r.content)
        return epub_path

    def convert_fb2_to_epub(self, fb2_path: Path, title: str = '', authors: str = '', fallback: str = 'book') -> Path:
        out_name = self.safe_book_filename(authors, title, fallback) + '.epub'
        epub = fb2_path.with_name(out_name)
        cmd = ['ebook-convert', str(fb2_path), str(epub)]
        if title:
            cmd += ['--title', title]
        if authors:
            cmd += ['--authors', authors]
        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return epub

    def send_epubs_by_email(self, tg_id: str, epub_paths: List[Path], users_path: Path, to_email_override: str | None = None) -> Tuple[int, str]:
        users = {}
        if users_path.exists():
            try:
                import json
                users = json.loads(users_path.read_text(encoding='utf-8'))
            except Exception:
                users = {}
        user_email = None
        if isinstance(users.get(str(tg_id)), dict):
            user_email = users.get(str(tg_id), {}).get('email')
        to_email = to_email_override or user_email
        if not to_email:
            raise RuntimeError(f'No target email in users.json for telegram id {tg_id}')

        smtp = self.load_smtp()
        batches = []
        cur, cur_size = [], 0
        for p in epub_paths:
            sz = p.stat().st_size
            if cur and cur_size + sz > self.max_email_bytes:
                batches.append(cur)
                cur, cur_size = [], 0
            cur.append(p)
            cur_size += sz
        if cur:
            batches.append(cur)

        for i, batch in enumerate(batches, 1):
            msg = EmailMessage()
            msg['From'] = smtp['FROM_EMAIL']
            msg['To'] = to_email
            msg['Subject'] = f'Library Bot books [{i}/{len(batches)}]'
            msg.set_content('Книги во вложении. Sent by library_bot.')
            for p in batch:
                msg.add_attachment(
                    p.read_bytes(),
                    maintype='application',
                    subtype='epub+zip',
                    filename=p.name,
                )
            with smtplib.SMTP_SSL(smtp['SMTP_HOST'], int(smtp['SMTP_PORT'])) as s:
                s.login(smtp['SMTP_USER'], smtp['SMTP_PASS'])
                s.send_message(msg)
        return len(batches), to_email
