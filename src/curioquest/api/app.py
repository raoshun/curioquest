"""FastAPI アプリケーション組成（03-architecture 12 のコントラクト）。

テキスト入力・取得だけを扱う受付。ここで事業ロジックの判断はせず、
エンジン・データストアを呼び、ドメイン例外を HTTP ステータスへ変換するだけにする。
ノイズを伴う 1 ターン（voice API）は voice.py に分離する。
"""

from __future__ import annotations

import hashlib
from datetime import UTC, date, datetime

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse

from curioquest.api.deps import AppComponents
from curioquest.api.schemas import (
    CharacterOut,
    ChildCreate,
    ChildOut,
    ChildPatch,
    DashboardChildOut,
    DashboardOut,
    MemoryOut,
    MessageIn,
    MissionOut,
    SessionStart,
    StampOut,
    StampsOut,
    TodayOut,
    TurnOut,
    character_out,
)
from curioquest.db.store import DataStore, NotFound
from curioquest.domain.models import Child, MemoryType, Mission
from curioquest.engine import DailyLimitExceeded, SessionClosed
from curioquest.llm import LLMError

API = "/api"


def _cq(request: Request) -> AppComponents:
    """リクエスト単位の依存の取り出し。テストは app.state.cq を差し替える。"""
    return request.app.state.cq


def _day_or_today(day: str | None) -> str:
    return day or date.today().isoformat()


def _local_day(finished_at: str) -> str:
    """SQLite datetime('now') の UTC 時刻文字列をサーバーローカルの日付文字列へ変換する。

    missions_today や usage_daily はサーバーローカル日付で台帳化しているため、
    完了時刻も同じ基準へ揃えてから照合する（JST 運用での深夜・早朝のずれ対策）。
    """
    ts = datetime.strptime(finished_at[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC)
    return ts.astimezone().date().isoformat()


def _resolve_today_mission(store: DataStore, child: Child, day: str) -> Mission:
    """今日のミッションを返す。未割当なら子と日付で決定的に 1 件を割り当てて保存する。

    乱数ではなく sha256 の残りなので、同じ子・同じ日なら何度でも同じ
    ミッションになり、サーバーを再起動しても変わらない（02-features 6-1）。
    """
    mission = store.get_today_mission(child.id, day)
    if mission is not None:
        return mission
    missions = store.list_missions()
    if not missions:
        raise NotFound("missions")
    digest = hashlib.sha256(f"{child.id}:{day}".encode()).hexdigest()
    chosen = missions[int(digest, 16) % len(missions)]
    store.set_today_mission(child.family_id, child.id, day, chosen.id)
    return store.get_mission(chosen.id)


def create_app(components: AppComponents) -> FastAPI:
    """アプリ組み立て。テスト・デモでは組み替えた AppComponents を渡す。"""
    app = FastAPI(title="CurioQuest API", version="0.1.0")
    app.state.cq = components

    # ------------------------------------------------------------ 例外処理

    @app.exception_handler(NotFound)
    def not_found_handler(_: Request, exc: NotFound) -> JSONResponse:
        return JSONResponse(status_code=404, content={"detail": f"データが見つかりません: {exc}"})

    @app.exception_handler(DailyLimitExceeded)
    def daily_limit_handler(_: Request, exc: DailyLimitExceeded) -> JSONResponse:
        return JSONResponse(
            status_code=409,
            content={
                "detail": "本日の利用時間に達しているため会話を開始できません",
                "child_id": exc.child_id,
                "daily_limit_minutes": exc.daily_limit_minutes,
            },
        )

    @app.exception_handler(SessionClosed)
    def session_closed_handler(_: Request, exc: SessionClosed) -> JSONResponse:
        return JSONResponse(
            status_code=409,
            content={"detail": f"この会話はすでに終了しています: {exc.session_id}"},
        )

    @app.exception_handler(LLMError)
    def llm_error_handler(_: Request, exc: LLMError) -> JSONResponse:
        return JSONResponse(
            status_code=502,
            content={"detail": f"応答生成に失敗しました。時間を置いて再試行: {exc}"},
        )

    @app.exception_handler(ValueError)
    def value_error_handler(_: Request, exc: ValueError) -> JSONResponse:
        return JSONResponse(status_code=400, content={"detail": str(exc)})

    # ------------------------------------------------------------ 基本

    @app.get("/healthz")
    def healthz(request: Request) -> dict[str, str]:
        """生存確認（コンテナのヘルスチェック用）。DB には触れない。"""
        assert _cq(request) is not None
        return {"status": "ok"}

    @app.get(f"{API}/children", response_model=list[ChildOut])
    def list_children(request: Request) -> list[ChildOut]:
        comp = _cq(request)
        return [ChildOut.from_domain(c) for c in comp.store.list_children(comp.family_id)]

    @app.post(f"{API}/children", response_model=ChildOut, status_code=201)
    def create_child(body: ChildCreate, request: Request) -> ChildOut:
        comp = _cq(request)
        child = comp.store.create_child(
            comp.family_id,
            nickname=body.nickname,
            birthday=body.birthday,
            favorite_topics=body.favorite_topics,
            favorite_character=body.favorite_character,
            daily_limit_minutes=body.daily_limit_minutes,
        )
        return ChildOut.from_domain(child)

    @app.get(f"{API}/children/{{child_id}}", response_model=ChildOut)
    def get_child(child_id: str, request: Request) -> ChildOut:
        return ChildOut.from_domain(_cq(request).store.get_child(child_id))

    @app.patch(f"{API}/children/{{child_id}}", response_model=ChildOut)
    def patch_child(child_id: str, body: ChildPatch, request: Request) -> ChildOut:
        patch = body.model_dump(exclude_unset=True)
        child = _cq(request).store.update_child(child_id, patch)
        return ChildOut.from_domain(child)

    @app.delete(f"{API}/children/{{child_id}}/history", status_code=204)
    def delete_history(child_id: str, request: Request) -> None:
        """保護者が会話履歴だけを削除する（03-architecture 11 親権対応）。"""
        store = _cq(request).store
        store.get_child(child_id)  # 存在確認（無ければ 404）
        store.delete_history_for_child(child_id)
        return None

    @app.get(f"{API}/characters", response_model=list[CharacterOut])
    def list_characters(request: Request) -> list[CharacterOut]:
        store = _cq(request).store
        return [character_out(c) for c in store.list_characters()]

    # ------------------------------------------------------------ ミッション・会話

    @app.get(f"{API}/missions/today", response_model=TodayOut)
    def get_today_mission(
        request: Request, child_id: str = Query(...), day: str | None = Query(default=None)
    ) -> TodayOut:
        comp = _cq(request)
        child = comp.store.get_child(child_id)
        resolved_day = _day_or_today(day)
        mission = _resolve_today_mission(comp.store, child, resolved_day)
        return TodayOut(day=resolved_day, mission=MissionOut.from_domain(mission))

    @app.post(f"{API}/sessions", response_model=TurnOut, status_code=201)
    def start_session(body: SessionStart, request: Request) -> TurnOut:
        comp = _cq(request)
        child = comp.store.get_child(body.child_id)
        resolved_day = _day_or_today(body.day)
        if body.mission_id is not None:
            mission = comp.store.get_mission(body.mission_id)
        else:
            mission = _resolve_today_mission(comp.store, child, resolved_day)
        character_id = body.character_id or mission.character_id
        result = comp.engine.start_session(child.id, mission.id, character_id, day=resolved_day)
        return TurnOut.from_result(result)

    @app.post(f"{API}/sessions/{{session_id}}/message", response_model=TurnOut)
    def post_message(session_id: str, body: MessageIn, request: Request) -> TurnOut:
        comp = _cq(request)
        result = comp.engine.handle_turn(
            session_id,
            body.text,
            seconds_spent=body.seconds_spent,
            day=_day_or_today(body.day),
        )
        return TurnOut.from_result(result)

    @app.post(f"{API}/sessions/{{session_id}}/complete", response_model=TurnOut)
    def complete_session(session_id: str, request: Request) -> TurnOut:
        result = _cq(request).engine.complete(session_id)
        return TurnOut.from_result(result)

    # ------------------------------------------------------------ 記録・保護者

    @app.get(f"{API}/children/{{child_id}}/stamps", response_model=StampsOut)
    def get_stamps(child_id: str, request: Request) -> StampsOut:
        store = _cq(request).store
        store.get_child(child_id)  # 存在確認
        earned = store.list_child_stamps(child_id)
        available = store.list_stamps()
        return StampsOut(
            available=len(available),
            earned=len(earned),
            stamps=[StampOut.from_domain(s) for s in earned],
        )

    @app.get(f"{API}/children/{{child_id}}/memories", response_model=list[MemoryOut])
    def get_memories(
        child_id: str, request: Request, memory_type: str | None = Query(default=None, alias="type")
    ) -> list[MemoryOut]:
        store = _cq(request).store
        store.get_child(child_id)  # 存在確認
        resolved: MemoryType | None
        try:
            resolved = MemoryType(memory_type) if memory_type is not None else None
        except ValueError as exc:
            raise HTTPException(
                status_code=422, detail=f"認識できないメモリの種類です: {memory_type}"
            ) from exc
        return [MemoryOut.from_domain(m) for m in store.list_memories(child_id, type=resolved)]

    @app.get(f"{API}/parent/dashboard", response_model=DashboardOut)
    def parent_dashboard(request: Request, day: str | None = Query(default=None)) -> DashboardOut:
        comp = _cq(request)
        store = comp.store
        resolved_day = _day_or_today(day)
        rows: list[DashboardChildOut] = []
        for child in store.list_children(comp.family_id):
            mission = store.get_today_mission(child.id, resolved_day)
            finished_today = sum(
                1
                for result in store.list_mission_results(child.id)
                if result.finished_at and _local_day(result.finished_at) == resolved_day
            )
            rows.append(
                DashboardChildOut(
                    child_id=child.id,
                    nickname=child.nickname,
                    today_mission_title=mission.title if mission else None,
                    used_seconds_today=store.get_used_seconds(child.id, resolved_day),
                    daily_limit_minutes=child.daily_limit_minutes,
                    missions_finished_today=finished_today,
                    stamps_earned=len(store.list_child_stamps(child.id)),
                )
            )
        return DashboardOut(day=resolved_day, children=rows)

    return app
