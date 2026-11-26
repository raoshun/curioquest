"""FastAPI アプリケーション組成(03-architecture 12 のコントラクト)。

テキスト入力・取得だけを扱う受付。ここで事業ロジックの判断はせず、
エンジン・データストアを呼び、ドメイン例外を HTTP ステータスへ変換するだけにする。
ノイズを伴う 1 ターン(voice API)は voice.py に分離する。
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse

from curioquest.api.deps import API, AppComponents, resolve_planner
from curioquest.api.deps import cq as _cq
from curioquest.api.deps import day_or_today as _day_or_today
from curioquest.api.schemas import (
    CharacterOut,
    ChildCreate,
    ChildOut,
    ChildPatch,
    ChildProgressOut,
    ConversationOut,
    DashboardChildOut,
    DashboardOut,
    InterestOut,
    MemoryOut,
    MessageIn,
    MissionHistoryOut,
    MissionOut,
    PinChangeIn,
    PinIn,
    PinStatusOut,
    SessionStart,
    StampOut,
    StampsOut,
    ThinkingOut,
    TodayOut,
    TurnOut,
    VoiceCacheOut,
    character_out,
)
from curioquest.api.security import (
    PIN_COOKIE,
    SESSION_TTL_SECONDS,
    ParentAuth,
    PinLocked,
)
from curioquest.api.streaming import add_streaming_routes
from curioquest.api.voice import add_voice_routes
from curioquest.audio import CachingTts
from curioquest.db.store import DataStore, NotFound
from curioquest.domain.models import Child, MemoryType, Mission
from curioquest.engine import DailyLimitExceeded, SessionClosed
from curioquest.llm import LLMError
from curioquest.utils.dates import to_utc_day


def _resolve_today_mission(comp: AppComponents, child: Child, day: str) -> Mission:
    """今日のミッションを返す。未割当なら planner が 1 件決めて割当を保存する。

    定番DBの決定的な抽選を土台に、その子の記憶が十分なら個人最適のお題を生成する (6-2, 6-3)。
    いずれにせよ同じ子・同じ日は絶対に同じお題になる。
    """
    return resolve_planner(comp).plan(child, day)


# デモ画面(static/index.html)。同じオリジンから API と音声ターンを呼ぶ。
_STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


def _file_in_static(rel_path: str) -> FileResponse:
    """静的ディレクトリの中からのみ返す(../ などで外に出させない)。"""
    root = _STATIC_DIR.resolve()
    target = (root / rel_path).resolve()
    if (root not in target.parents) or not target.is_file():
        raise HTTPException(status_code=404, detail="ページが見つからなかったよ")
    return FileResponse(target, headers={"Cache-Control": "no-cache"})


_RECENT_VIEW_LIMIT = 5


def _progress_of(store: DataStore, child: Child, day: str) -> ChildProgressOut:
    """今日進行状況の共通算出。子画面と保護者画面で同じ数字を出すために寄せる。"""
    results = store.list_mission_results(child.id)
    used = store.get_used_seconds(child.id, day)
    limit = child.daily_limit_minutes
    mission = store.get_today_mission(child.id, day)
    return ChildProgressOut(
        day=day,
        today_mission=MissionOut.from_domain(mission) if mission else None,
        used_seconds_today=used,
        daily_limit_minutes=limit,
        remaining_seconds=max(limit * 60 - used, 0) if limit > 0 else None,
        missions_finished_today=sum(
            1 for result in results if to_utc_day(result.finished_at) == day
        ),
        missions_finished_total=len(results),
        stamps_earned=len(store.list_child_stamps(child.id)),
    )


def _top_interests(
    store: DataStore, child_id: str, limit: int = _RECENT_VIEW_LIMIT
) -> list[InterestOut]:
    """記憶と「好きなこと」から今いちばん気にしている話題を数え上げる (6-3)。"""
    counts: Counter[str] = Counter()
    for memory in store.list_memories(child_id):
        if memory.topic:
            counts[memory.topic] += 1
        elif memory.type is MemoryType.PREFERENCE:
            counts[memory.content] += 1
    return [InterestOut(topic=topic, count=count) for topic, count in counts.most_common(limit)]


def _parent_auth(request: Request) -> ParentAuth:
    auth: ParentAuth = request.app.state.parent_auth
    return auth


def _require_parent(request: Request) -> None:
    """PIN が設定されている時だけ、未確認の保護者エリアアクセスを締める (02-features 3-3)。"""
    auth = _parent_auth(request)
    if not auth.is_set():
        return
    if not auth.authenticated(request.cookies.get(PIN_COOKIE)):
        raise HTTPException(
            status_code=401,
            detail="保護者エリアは PIN でロックされています。PIN を入力してください",
        )


def _grant_parent(auth: ParentAuth, payload: dict[str, bool]) -> JSONResponse:
    """PIN 認証成功のしるしにトークンを発行し、httpOnly Cookie で返す。"""
    response = JSONResponse(payload)
    response.set_cookie(
        PIN_COOKIE,
        auth.start_session(),
        max_age=SESSION_TTL_SECONDS,
        httponly=True,
        samesite="lax",
        path="/",
    )
    return response


def _revoke_parent(auth: ParentAuth, payload: dict[str, bool]) -> JSONResponse:
    response = JSONResponse(payload)
    response.delete_cookie(PIN_COOKIE, path="/")
    return response


def create_app(components: AppComponents) -> FastAPI:
    """アプリ組み立て。テスト・デモでは組み替えた AppComponents を渡す。"""
    app = FastAPI(title="CurioQuest API", version="0.1.0")
    app.state.cq = components
    # 保護者エリアの PIN は 1 家族 1 件(設定はストアの setting に保存)
    app.state.parent_auth = ParentAuth(components.store, components.family_id)

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

    @app.exception_handler(PinLocked)
    def pin_locked_handler(_: Request, exc: PinLocked) -> JSONResponse:
        return JSONResponse(
            status_code=429,
            content={"detail": "PIN 失敗が続いているよ。しばらくしてからもう一度試してね"},
        )

    # ------------------------------------------------------------ 基本

    @app.get("/", include_in_schema=False)
    def home_page() -> FileResponse:
        """子ども向けホーム画面。保護者画面は /parent、最小検証ページは /curio に置いた。"""
        return _file_in_static("index.html")

    @app.get("/parent", include_in_schema=False)
    def parent_page() -> FileResponse:
        """保護者ダッシュボード。PIN で守られる (02-features 3-3)。"""
        return _file_in_static("parent.html")

    @app.get("/curio", include_in_schema=False)
    def curio_page() -> FileResponse:
        """従来からの最小検証ページ。実機確認用の器具なので経路を変えて残した。"""
        return _file_in_static("assets/curio.html")

    @app.get("/healthz")
    def healthz(request: Request) -> dict[str, str]:
        """生存確認(コンテナのヘルスチェック用)。DB には触れない。"""
        assert _cq(request) is not None
        return {"status": "ok"}

    @app.get(f"{API}/children", response_model=list[ChildOut])
    def list_children(request: Request) -> list[ChildOut]:
        comp = _cq(request)
        return [ChildOut.from_domain(c) for c in comp.store.list_children(comp.family_id)]

    @app.post(f"{API}/children", response_model=ChildOut, status_code=201)
    def create_child(body: ChildCreate, request: Request) -> ChildOut:
        _require_parent(request)
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
        _require_parent(request)
        patch = body.model_dump(exclude_unset=True)
        child = _cq(request).store.update_child(child_id, patch)
        return ChildOut.from_domain(child)

    @app.delete(f"{API}/children/{{child_id}}/history", status_code=204)
    def delete_history(child_id: str, request: Request) -> None:
        """保護者が会話履歴だけを削除する(03-architecture 11 親権対応)。"""
        _require_parent(request)
        store = _cq(request).store
        store.get_child(child_id)  # 存在確認(無ければ 404)
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
        mission = _resolve_today_mission(comp, child, resolved_day)
        return TodayOut(day=resolved_day, mission=MissionOut.from_domain(mission))

    @app.post(f"{API}/sessions", response_model=TurnOut, status_code=201)
    def start_session(body: SessionStart, request: Request) -> TurnOut:
        comp = _cq(request)
        child = comp.store.get_child(body.child_id)
        resolved_day = _day_or_today(body.day)
        if body.mission_id is not None:
            mission = comp.store.get_mission(body.mission_id)
        else:
            mission = _resolve_today_mission(comp, child, resolved_day)
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

    @app.get(f"{API}/children/{{child_id}}/progress", response_model=ChildProgressOut)
    def get_child_progress(
        child_id: str, request: Request, day: str | None = Query(default=None)
    ) -> ChildProgressOut:
        """今日やること・残り時間。子画面ホームと保護者画面が同じ数字を読む (AC-04)。"""
        comp = _cq(request)
        child = comp.store.get_child(child_id)  # 存在確認
        return _progress_of(comp.store, child, _day_or_today(day))

    @app.get(f"{API}/children/{{child_id}}/conversations", response_model=list[ConversationOut])
    def get_child_conversations(
        child_id: str, request: Request, limit: int = Query(default=30, ge=1, le=200)
    ) -> list[ConversationOut]:
        """会話一覧(いつ・どのミッションを・何ターン)。保護者画面 9-2 の行。"""
        _require_parent(request)
        store = _cq(request).store
        store.get_child(child_id)  # 存在確認
        return [
            ConversationOut.from_summary(summary)
            for summary in store.list_session_summaries(child_id, limit=limit)
        ]

    @app.delete(f"{API}/parent/voice-cache", response_model=VoiceCacheOut)
    def delete_voice_cache(request: Request) -> VoiceCacheOut:
        """TTS 音声キャッシュを全消去 (Issue #6、05-safety-quality の削除経路)。

        消えるのは定型文などの合成済み WAV だけで、会話テキストや記録には
        触れない。次回以降のターンで必要な分だけ作り直される。
        """
        _require_parent(request)
        tts = _cq(request).tts
        deleted = tts.clear() if isinstance(tts, CachingTts) else 0
        return VoiceCacheOut(deleted=deleted)

    @app.get(f"{API}/parent/pin/status", response_model=PinStatusOut)
    def get_pin_status(request: Request) -> PinStatusOut:
        """PIN が設定済みか・今この端末が保護者認証済みか(保護者画面の入口表示用)。"""
        auth = _parent_auth(request)
        return PinStatusOut(
            is_set=auth.is_set(), authenticated=auth.authenticated(request.cookies.get(PIN_COOKIE))
        )

    @app.post(f"{API}/parent/pin", responses={403: {"description": "すでに PIN 設定済み"}})
    def setup_pin(body: PinIn, request: Request) -> JSONResponse:
        """はじめての PIN 設定。設定済みならこのルートでは変更できない(変更は /change)。"""
        auth = _parent_auth(request)
        if auth.is_set():
            raise HTTPException(
                status_code=403,
                detail="PIN はすでに設定済みです。変更は /api/parent/pin/change を使ってね",
            )
        auth.setup(body.pin)
        return _grant_parent(auth, PinStatusOut(is_set=True, authenticated=True).model_dump())

    @app.post(f"{API}/parent/pin/verify")
    def verify_pin(body: PinIn, request: Request) -> JSONResponse:
        """PIN 入力による保護者認証。成功すると保護者用 Cookie を発行する。"""
        auth = _parent_auth(request)
        if not auth.is_set():
            raise HTTPException(status_code=409, detail="先に PIN を設定してください")
        if not auth.verify(body.pin):
            raise HTTPException(status_code=401, detail="PIN が違います")
        return _grant_parent(auth, PinStatusOut(is_set=True, authenticated=True).model_dump())

    @app.post(f"{API}/parent/pin/change")
    def change_pin(body: PinChangeIn, request: Request) -> JSONResponse:
        """PIN 変更。現在の PIN が必要で、成功すると他の端末のログインはすべて無効になる。"""
        auth = _parent_auth(request)
        _require_parent(request)
        auth.change(body.old_pin, body.new_pin)
        # change で全セッション失効するため、この端末だけ新しい Cookie を受け取る
        return _grant_parent(auth, PinStatusOut(is_set=True, authenticated=True).model_dump())

    @app.post(f"{API}/parent/pin/logout")
    def logout_pin(request: Request) -> JSONResponse:
        """この端末の保護者認証だけを解除する。"""
        auth = _parent_auth(request)
        auth.end_session(request.cookies.get(PIN_COOKIE))
        return _revoke_parent(
            auth, PinStatusOut(is_set=auth.is_set(), authenticated=False).model_dump()
        )

    @app.get(f"{API}/parent/dashboard", response_model=DashboardOut)
    def parent_dashboard(request: Request, day: str | None = Query(default=None)) -> DashboardOut:
        """保護者画面の主データ。進行状況に加えて興味・考え・最近の記録を一括で出す。"""
        _require_parent(request)
        comp = _cq(request)
        store = comp.store
        resolved_day = _day_or_today(day)
        mission_titles = {mission.id: mission.title for mission in store.list_missions()}
        rows: list[DashboardChildOut] = []
        for child in store.list_children(comp.family_id):
            progress = _progress_of(store, child, resolved_day)
            rows.append(
                DashboardChildOut(
                    child_id=child.id,
                    nickname=child.nickname,
                    today_mission_title=(
                        progress.today_mission.title if progress.today_mission else None
                    ),
                    used_seconds_today=progress.used_seconds_today,
                    daily_limit_minutes=progress.daily_limit_minutes,
                    missions_finished_today=progress.missions_finished_today,
                    stamps_earned=progress.stamps_earned,
                    interests=_top_interests(store, child.id),
                    recent_thinking=[
                        ThinkingOut.from_record(record)
                        for record in list(reversed(store.list_thinking_records(child.id)))[
                            :_RECENT_VIEW_LIMIT
                        ]
                    ],
                    recent_missions=[
                        MissionHistoryOut.from_result(
                            result,
                            mission_titles.get(result.mission_id, result.mission_id),
                            to_utc_day(result.finished_at),
                        )
                        for result in store.list_mission_results(child.id)[:_RECENT_VIEW_LIMIT]
                    ],
                    recent_conversations=[
                        ConversationOut.from_summary(summary)
                        for summary in store.list_session_summaries(
                            child.id, limit=_RECENT_VIEW_LIMIT
                        )
                    ],
                )
            )
        return DashboardOut(day=resolved_day, children=rows)

    add_voice_routes(app)
    add_streaming_routes(app)

    return app
