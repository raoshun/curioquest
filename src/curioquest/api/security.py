"""保護者エリアの PIN 保護（02-features 3-3）。

PIN は塩がかり PBKDF2 で app_settings に保存し、平文はどこにも残さない。
認証に通るとランダムなトークンを Cookie に載せ、ストア側で有効つき管理する。
失敗を数えるカウンタだけメモリ上にある（家庭内の単一プロセス前提）。
PIN が未設定のうちは保護しない。既存のご家庭をいきなり締め出さないための方針。
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import time

PIN_COOKIE = "cq_parent_session"
PIN_SETTING_KEY = "parent_pin"
SESSIONS_SETTING_KEY = "parent_sessions"

_PIN_MIN = 4
_PIN_MAX = 8
_ITERATIONS = 200_000
_FAILED_LIMIT = 5
SESSION_TTL_SECONDS = 2 * 3600  # 2時間たつと、また PIN を聞く


class PinFormat(ValueError):
    """PIN が形になっていない（4〜8 桁の数字ではない）。"""


class PinLocked(Exception):
    """失敗続きで、しばらく PIN 入力をあずかる。"""


def validate_pin(pin: str) -> None:
    if not (pin.isdigit() and _PIN_MIN <= len(pin) <= _PIN_MAX):
        raise PinFormat(f"PIN は {_PIN_MIN} 桁以上 {_PIN_MAX} 桁以下の数字で入力してください")


def _hash_pin(pin: str, salt: bytes) -> bytes:
    return hashlib.pbkdf2_hmac("sha256", pin.encode("utf-8"), salt, _ITERATIONS)


def _matches(pin: str, record: dict) -> bool:
    try:
        salt = bytes.fromhex(record["salt"])
        expected = bytes.fromhex(record["hash"])
    except (KeyError, ValueError, TypeError):
        return False
    return hmac.compare_digest(_hash_pin(pin, salt), expected)


class ParentAuth:
    """1 家族ぶんの PIN 認証。PIN とセッションはストア、失敗カウンタはメモリ置き。"""

    def __init__(self, store, family_id: str, *, block_seconds: int = 60) -> None:
        self._store = store
        self._family_id = family_id
        self._block_seconds = block_seconds
        self._failed = 0
        self._locked_until = 0.0

    # ------------------------------------------------------------ 状態

    def is_set(self) -> bool:
        return self._store.get_setting(self._family_id, PIN_SETTING_KEY) is not None

    # ------------------------------------------------------------ 設定・確認

    def setup(self, pin: str) -> None:
        """初めての PIN を登録する。設定済みなら呼び出し側で弾く想定。"""
        validate_pin(pin)
        if self.is_set():
            raise PinFormat("PIN はもう設定済みです。変更で今の PIN を確認してください")
        self._save_pin(pin)

    def change(self, old_pin: str, new_pin: str) -> None:
        """今の PIN 確認できてから、新しい PIN に置き換える。他の端末の認証もやり直しになる。"""
        validate_pin(new_pin)
        if self.is_set() and not self.verify(old_pin):
            raise PinFormat("現在の PIN が違います")
        self._save_pin(new_pin)
        self._store.set_setting(self._family_id, SESSIONS_SETTING_KEY, {})

    def verify(self, pin: str) -> bool:
        """PIN があっていれば True。違いなら失敗を数え、失敗続きなら PinLocked。"""
        if time.time() < self._locked_until:
            raise PinLocked("失敗が続いているので、しばらくしてからもう一度試してね")
        record = self._store.get_setting(self._family_id, PIN_SETTING_KEY)
        if isinstance(record, dict) and _matches(pin, record):
            self._reset_failures()
            return True
        self._register_failure()
        return False

    # ------------------------------------------------------------ セッション

    def start_session(self) -> str:
        """保護者セッショントークンを作って保存し、Cookie に載せる値を返す。"""
        token = secrets.token_urlsafe(32)
        sessions = self._clean_sessions()
        sessions[token] = time.time() + SESSION_TTL_SECONDS
        self._store.set_setting(self._family_id, SESSIONS_SETTING_KEY, sessions)
        return token

    def authenticated(self, token: str | None) -> bool:
        if not token:
            return False
        expires = self._clean_sessions().get(token)
        return bool(expires and expires > time.time())

    def end_session(self, token: str | None) -> None:
        if not token:
            return
        sessions = self._clean_sessions()
        sessions.pop(token, None)
        self._store.set_setting(self._family_id, SESSIONS_SETTING_KEY, sessions)

    # ------------------------------------------------------------ 内部処理

    def _save_pin(self, pin: str) -> None:
        salt = secrets.token_bytes(16)
        record = {"salt": salt.hex(), "hash": _hash_pin(pin, salt).hex()}
        self._store.set_setting(self._family_id, PIN_SETTING_KEY, record)
        self._reset_failures()

    def _reset_failures(self) -> None:
        self._failed = 0
        self._locked_until = 0.0

    def _register_failure(self) -> None:
        self._failed += 1
        if self._failed >= _FAILED_LIMIT:
            self._locked_until = time.time() + self._block_seconds
            self._failed = 0

    def _clean_sessions(self) -> dict[str, float]:
        saved = self._store.get_setting(self._family_id, SESSIONS_SETTING_KEY)
        if not isinstance(saved, dict):
            return {}
        now = time.time()
        return {t: v for t, v in saved.items() if isinstance(v, float) and v > now}
