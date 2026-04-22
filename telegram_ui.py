from __future__ import annotations

import re
from typing import List


def books_human_list(books: List[dict]) -> str:
    chunks = []
    for b in books:
        title = (b.get('title') or 'Без названия').strip()
        authors = (b.get('authors') or 'Автор не указан').strip()
        chunks.append(f'{title} — {authors}')
    return '; '.join(chunks)


def normalize_email(value: str) -> str:
    e = value.strip()
    e = re.sub(r'\s+', '', e)
    e = e.replace('..', '.')
    return e


def is_valid_email(value: str) -> bool:
    e = normalize_email(value)
    return bool(re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", e))


def render_search_page(found: List[dict], page: int, page_size: int) -> str:
    page_size = max(1, page_size)
    total = len(found)
    total_pages = max(1, (total + page_size - 1) // page_size)
    page = max(1, min(page, total_pages))
    start = (page - 1) * page_size
    chunk = found[start:start + page_size]

    lines = []
    for i, b in enumerate(chunk, start=start + 1):
        title = b.get('title', '').strip() or 'Без названия'
        authors = (b.get('authors') or '').strip() or 'Автор не указан'
        lines.append(f"{i}. {title}")
        lines.append(authors)
        lines.append(f"Номер для отправки: {i}")
    lines.append('')
    lines.append('Для отправки книг введите их номера.')
    return '\n'.join(lines)


def pagination_keyboard(page: int, total_items: int, page_size: int) -> dict | None:
    page_size = max(1, page_size)
    total_pages = max(1, (total_items + page_size - 1) // page_size)
    if total_pages <= 1:
        return None

    page = max(1, min(page, total_pages))
    buttons = []
    if page > 1:
        buttons.append({'text': '<', 'callback_data': f'pg:{page - 1}'})
    if page - 1 >= 1:
        buttons.append({'text': str(page - 1), 'callback_data': f'pg:{page - 1}'})
    buttons.append({'text': f'• {page} •', 'callback_data': f'pg:{page}'})
    if page + 1 <= total_pages:
        buttons.append({'text': str(page + 1), 'callback_data': f'pg:{page + 1}'})
    if page < total_pages:
        buttons.append({'text': '>', 'callback_data': f'pg:{page + 1}'})

    return {'inline_keyboard': [buttons]}
