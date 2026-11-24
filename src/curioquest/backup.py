"""SQLite データベースの自動バックアップ（Phase 5 家庭内実運用）。

家庭の資産（スタンプ・Memory・会話履歴）をディスク故障や誤操作から守る。
sqlite3 のオンラインバックアップ API を使うため、サーバーを止めたままでも
整合性の取れたスナップショットを取れる。compose の `backup` サイドカーから
定期的に呼ぶ運用を想定する。

- スナップショットは gzip 圧縮で ``curioquest-YYYYmmdd-HHMMSS.sqlite.gz`` 名で保存
- 世代数 (keep) を超えた古いアーカイブは新しい順に自動削除
- 復元は ``curioquest-backup --restore``（サーバー停止中に行うこと）

環境変数:
    CURIOQUEST_BACKUP_KEEP                  世代数（既定 7、0 以下で無制限）
    CURIOQUEST_BACKUP_INTERVAL_SECONDS      ループ実行の間隔秒（既定 0=1回だけ）
    CURIOQUEST_BACKUP_DIR                   保存先（既定は DB と同じ階層の backups/）
"""

from __future__ import annotations

import argparse
import gzip
import os
import shutil
import sqlite3
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

from curioquest.config import ENV_DB_PATH

ARCHIVE_PREFIX = "curioquest-"
ARCHIVE_SUFFIX = ".sqlite.gz"
DEFAULT_KEEP = 7

ENV_BACKUP_KEEP = "CURIOQUEST_BACKUP_KEEP"
ENV_BACKUP_INTERVAL = "CURIOQUEST_BACKUP_INTERVAL_SECONDS"
ENV_BACKUP_DIR = "CURIOQUEST_BACKUP_DIR"


def backup_database(
    db_path: str | Path,
    dest_dir: str | Path,
    *,
    keep: int = DEFAULT_KEEP,
    now: datetime | None = None,
) -> Path:
    """実行中でも整合性の取れたスナップショットを gzip で保存する。

    戻り値は作成したアーカイブパス。keep が正の整数なら、保存先の
    既存アーカイブを新しい順 keep 世代に保つ（0 以下は無制限）。
    """
    src = Path(db_path)
    if not src.is_file():
        raise FileNotFoundError(f"データベースが見つかりません: {src}")
    dest = Path(dest_dir)
    dest.mkdir(parents=True, exist_ok=True)

    stamp = (now or datetime.now(UTC)).strftime("%Y%m%d-%H%M%S")
    final = _free_archive_path(dest, stamp)
    snapshot = dest / f".{final.name}.snapshot.tmp"
    try:
        # 実行中の DB は読取専用で開き、一時ファイルへ一貫したコピーを作る
        with (
            sqlite3.connect(src.resolve().as_uri() + "?mode=ro", uri=True) as source,
            sqlite3.connect(snapshot) as copy,
        ):
            source.backup(copy)
        with snapshot.open("rb") as raw, gzip.open(final, "wb") as archive:
            shutil.copyfileobj(raw, archive)
    except BaseException:
        final.unlink(missing_ok=True)  # 失敗した途中ファイルを保持しない
        raise
    finally:
        snapshot.unlink(missing_ok=True)

    prune_old_backups(dest, keep=keep)
    return final


def prune_old_backups(dest_dir: str | Path, *, keep: int) -> list[Path]:
    """保存先のアーカイブを新しい順 keep 世代に整え、削除したパスを返す。"""
    if keep <= 0:
        return []
    archives = sorted(Path(dest_dir).glob(f"{ARCHIVE_PREFIX}*{ARCHIVE_SUFFIX}"))
    outdated = archives[:-keep] if len(archives) > keep else []
    for path in outdated:
        path.unlink(missing_ok=True)
    return outdated


def restore_backup(archive_path: str | Path, db_path: str | Path) -> Path:
    """アーカイブからデータベースを復元する（サーバー停止中に実行すること）。

    展開後に PRAGMA integrity_check を確認してから本差し替えするので、
    壊れたアーカイブで DB を汚さない。既存の DB は置き換わる。
    """
    archive = Path(archive_path)
    if not archive.is_file():
        raise FileNotFoundError(f"バックアップアーカイブが見つかりません: {archive}")
    target = Path(db_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(f".{target.name}.restore.tmp")
    try:
        with gzip.open(archive, "rb") as source, tmp.open("wb") as out:
            shutil.copyfileobj(source, out)
        _check_integrity(tmp)
        os.replace(tmp, target)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    return target


def _check_integrity(db_file: Path) -> None:
    with sqlite3.connect(db_file) as conn:
        (result,) = conn.execute("PRAGMA integrity_check").fetchone()
    if result != "ok":
        raise ValueError(f"バックアップの整合性チェックに失敗しました: {result}")


def _free_archive_path(dest: Path, stamp: str) -> Path:
    """同一秒に 2 回備えたときの連番付きも含めて衝突しない名前を選ぶ。"""
    candidate = dest / f"{ARCHIVE_PREFIX}{stamp}{ARCHIVE_SUFFIX}"
    n = 0
    while candidate.exists():
        n += 1
        candidate = dest / f"{ARCHIVE_PREFIX}{stamp}-{n}{ARCHIVE_SUFFIX}"
    return candidate


def _log(message: str) -> None:
    stamp = datetime.now(UTC).isoformat(timespec="seconds")
    print(f"[curioquest-backup {stamp}] {message}", flush=True)


def main(argv: list[str] | None = None) -> int:
    """`curioquest-backup` コンソールコマンド。1回実行または間隔ループで動く。"""
    parser = argparse.ArgumentParser(
        prog="curioquest-backup",
        description="CurioQuest の SQLite データベースをバックアップ・復元する",
    )
    parser.add_argument(
        "--db",
        default=os.environ.get(ENV_DB_PATH, "data/curioquest.db"),
        help="対象データベースのパス",
    )
    parser.add_argument(
        "--dest",
        default=None,
        help=f"バックアップ保存先（既定: 環境変数 {ENV_BACKUP_DIR} か DB と同階層の backups/）",
    )
    parser.add_argument(
        "--keep",
        type=int,
        default=_env_int(ENV_BACKUP_KEEP, DEFAULT_KEEP),
        help="保持する世代数（0 以下で無制限）",
    )
    parser.add_argument(
        "--interval",
        type=int,
        default=_env_int(ENV_BACKUP_INTERVAL, 0),
        help="0 より大でその秒数ごとに繰り返し実行する",
    )
    parser.add_argument(
        "--restore",
        metavar="ARCHIVE",
        default=None,
        help="指定アーカイブから --db へ復元して終了する",
    )
    args = parser.parse_args(argv)

    db = Path(args.db)
    dest = Path(args.dest or os.environ.get(ENV_BACKUP_DIR) or db.parent / "backups")

    if args.restore:
        try:
            restore_backup(args.restore, db)
        except (OSError, sqlite3.Error, ValueError) as exc:
            print(f"復元に失敗しました: {exc}", file=sys.stderr)
            return 1
        print(f"{db} へ復元しました（サーバーを再起動してください）")
        return 0

    if args.interval > 0:
        while True:
            _run_once(db, dest, keep=args.keep)
            time.sleep(args.interval)
    return _run_once(db, dest, keep=args.keep)


def _run_once(db: Path, dest: Path, *, keep: int) -> int:
    try:
        created = backup_database(db, dest, keep=keep)
    except (FileNotFoundError, OSError, sqlite3.Error) as exc:
        # サイドカーで走り続けられるよう、ここは例外を投げずに失敗を報告する
        _log(f"バックアップに失敗しました: {exc}")
        return 1
    _log(f"バックアップを作成しました: {created}")
    return 0


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    try:
        return int(raw) if raw else default
    except ValueError:
        return default


if __name__ == "__main__":
    raise SystemExit(main())
