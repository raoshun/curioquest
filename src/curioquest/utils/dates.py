"""日付まわりの小さな補助。

SQLite の既定時刻は `datetime('now')`（UTC・秒精度）だが、missions_today や
usage_daily などの台帳はサーバーローカル日付で持っている。子どもにとっての
「今日」を一致させるため、保存時刻は必ずローカル日付へ直してから扱う。
"""

from __future__ import annotations

from datetime import UTC, datetime


def to_utc_day(value: str | None) -> str | None:
    """DB の UTC 時刻文字列をサーバーローカル日付 ('YYYY-MM-DD') へ変換する。

    読み替えに失敗した場合（NULL・独自の書式など）は None を返す。
    """
    if not value:
        return None
    try:
        ts = datetime.strptime(value[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC)
    except ValueError:
        return None
    return ts.astimezone().date().isoformat()
