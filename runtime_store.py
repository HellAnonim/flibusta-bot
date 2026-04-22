from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class RuntimeStore:
    def __init__(self, runtime_dir: Path):
        self.runtime_dir = runtime_dir
        self.runtime_dir.mkdir(parents=True, exist_ok=True)
        self.inpx_meta_path = runtime_dir / 'inpx_meta.json'
        self.last_results_path = runtime_dir / 'last_results_by_tg.json'
        self.dialog_state_path = runtime_dir / 'dialog_state_by_tg.json'
        self.auth_state_path = runtime_dir / 'auth_state_by_tg.json'
        self.user_prefs_path = runtime_dir / 'user_prefs_by_tg.json'
        self.sent_index_path = runtime_dir / 'sent_books_by_tg.json'

    @staticmethod
    def load_json(path: Path) -> dict[str, Any]:
        if not path.exists():
            return {}
        return json.loads(path.read_text(encoding='utf-8'))

    @staticmethod
    def write_json_atomic(path: Path, data: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + '.tmp')
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
        tmp.replace(path)

    def load_inpx_meta(self) -> dict[str, Any]:
        return self.load_json(self.inpx_meta_path)

    def save_inpx_meta(self, data: dict[str, Any]) -> None:
        self.write_json_atomic(self.inpx_meta_path, data)

    def load_last_results(self) -> dict[str, list[dict[str, Any]]]:
        return self.load_json(self.last_results_path)

    def save_last_results(self, data: dict[str, list[dict[str, Any]]]) -> None:
        self.write_json_atomic(self.last_results_path, data)

    def load_dialog_state(self) -> dict[str, dict[str, Any]]:
        return self.load_json(self.dialog_state_path)

    def save_dialog_state(self, data: dict[str, dict[str, Any]]) -> None:
        self.write_json_atomic(self.dialog_state_path, data)

    def load_auth_state(self) -> dict[str, dict[str, Any]]:
        return self.load_json(self.auth_state_path)

    def save_auth_state(self, data: dict[str, dict[str, Any]]) -> None:
        self.write_json_atomic(self.auth_state_path, data)

    def load_user_prefs(self) -> dict[str, dict[str, Any]]:
        return self.load_json(self.user_prefs_path)

    def save_user_prefs(self, data: dict[str, dict[str, Any]]) -> None:
        self.write_json_atomic(self.user_prefs_path, data)

    def load_sent_index(self) -> dict[str, dict[str, Any]]:
        return self.load_json(self.sent_index_path)

    def save_sent_index(self, data: dict[str, dict[str, Any]]) -> None:
        self.write_json_atomic(self.sent_index_path, data)
