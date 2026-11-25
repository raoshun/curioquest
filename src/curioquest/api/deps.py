"""FastAPI アプリの依存性配線。

本番は Settings から本物のコンポーネントを組み立て、
テスト・デモでは AppComponents を組み替えて差し替えられるようにする。
MVP は単一家族（家庭内利用）を前提とし、家族IDはシーード済み既定値を使う（03-architecture 12）。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path

from fastapi import Request

from curioquest.audio import SpeechToText, TextToSpeech
from curioquest.config import Settings, settings_from_env
from curioquest.db.database import Database
from curioquest.db.seed import seed_default_data
from curioquest.db.store import DataStore
from curioquest.engine import DailyMissionPlanner, DialogueEngine, MissionPlanner
from curioquest.llm.factory import provider_from_settings

API = "/api"


def cq(request: Request) -> AppComponents:
    """リクエスト単位で組み込まれた依存の取り出し。テストは app.state.cq を差し替える。"""
    return request.app.state.cq


def day_or_today(day: str | None) -> str:
    """日付未指定ならサーバーローカルの今日を返す（テストでは day で固定できる）。"""
    return day or date.today().isoformat()


@dataclass
class AppComponents:
    """アプリが使うライフサイクル依存のまとまり。"""

    store: DataStore
    engine: DialogueEngine
    family_id: str
    planner: MissionPlanner | None = None
    stt: SpeechToText | None = None
    tts: TextToSpeech | None = None


def resolve_planner(components: AppComponents) -> MissionPlanner:
    """今日のミッションを決める planner を取り出す。

    組み立て時に渡していない場合（テストや小規模な組み替え）はその場で作る。
    対話エンジンと同じ LLM を使うので、応答とお題が同じ子モデルから出る。
    """
    if components.planner is not None:
        return components.planner
    return DailyMissionPlanner(store=components.store, llm=components.engine.llm)


def build_components(settings: Settings | None = None) -> AppComponents:
    """Settings から本物のコンポーネントを組み立てる（アプリ起動時のみ実行）。

    STT/TTS は遅延初期化のため、ここで生成しても認識モデルのダウンロードや
    音声エンジンへの接続は発生しない（使われた瞬間に初めて接続する）。
    """
    resolved = settings or settings_from_env()
    if resolved.db_path != ":memory:":
        Path(resolved.db_path).expanduser().absolute().parent.mkdir(parents=True, exist_ok=True)
    db = Database(resolved.db_path)
    db.init_schema()
    store = DataStore(db)
    family_id = seed_default_data(store)
    engine = DialogueEngine(store=store, llm=provider_from_settings(resolved))
    planner = DailyMissionPlanner(
        store=store,
        llm=engine.llm,
        generation_enabled=resolved.mission_generation_enabled,
    )
    stt, tts = build_audio(resolved)
    return AppComponents(
        store=store,
        engine=engine,
        family_id=family_id,
        planner=planner,
        stt=stt,
        tts=tts,
    )


def build_audio(settings: Settings) -> tuple[SpeechToText, TextToSpeech]:
    """設定から音声アダプタを生成する（内部は遅延接続なので安価）。"""
    # 局所 import: テストで sys.modules を差し替えて失敗経路を検証できるようにする
    from curioquest.audio import CachingTts, FasterWhisperStt, VoiceVoxTts

    stt = FasterWhisperStt(model_name=settings.stt_model)
    tts = VoiceVoxTts(
        base_url=settings.voicevox_base_url,
        default_speaker_id=settings.voicevox_default_speaker,
    )
    if settings.tts_cache_enabled:
        # db と同じデータ領域に WAV キャッシュを置く (08-voice-cache 13-3)
        cache_dir = settings.tts_cache_dir or str(
            Path(settings.db_path).expanduser().absolute().parent / "tts_cache"
        )
        tts = CachingTts(tts, cache_dir, default_speaker_id=settings.voicevox_default_speaker)
    return stt, tts
