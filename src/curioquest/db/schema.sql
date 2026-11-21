-- CurioQuest MVP スキーマ（設計仕様 03-architecture.md 第11章）
-- 音声データは保存しない：STT でテキスト化した後に破棄する方針のため、
-- 音声を格納するテーブル・カラムは意図的に存在させない。

PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS families (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS children (
    id                 TEXT PRIMARY KEY,
    family_id          TEXT NOT NULL REFERENCES families (id) ON DELETE CASCADE,
    nickname           TEXT NOT NULL,
    birthday           TEXT,                       -- ISO date. 参考情報であり難易度を自動決定する唯一の要素にはしない
    favorite_topics    TEXT NOT NULL DEFAULT '[]', -- JSON 配列
    favorite_character TEXT,
    difficulty_profile TEXT NOT NULL DEFAULT '{}', -- JSON (領域別難易度)
    daily_limit_minutes INTEGER NOT NULL DEFAULT 20,
    created_at         TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS characters (
    id             TEXT PRIMARY KEY,
    name           TEXT NOT NULL,
    description    TEXT NOT NULL DEFAULT '',
    personality    TEXT NOT NULL DEFAULT '[]',   -- JSON 配列
    thinking_style TEXT NOT NULL DEFAULT '[]',   -- JSON 配列
    system_prompt  TEXT NOT NULL,
    voice_engine   TEXT NOT NULL DEFAULT 'voicevox',
    voice_speaker_id INTEGER,
    voice_speed_scale REAL NOT NULL DEFAULT 1.0,
    voice_pitch_scale REAL NOT NULL DEFAULT 0.0
);

CREATE TABLE IF NOT EXISTS missions (
    id                    TEXT PRIMARY KEY,
    category              TEXT NOT NULL, -- curiosity | reasoning | imagination
    title                 TEXT NOT NULL,
    difficulty            INTEGER NOT NULL DEFAULT 1,
    target_skills         TEXT NOT NULL DEFAULT '[]', -- JSON 配列
    character_id          TEXT NOT NULL REFERENCES characters (id),
    estimated_minutes     INTEGER NOT NULL DEFAULT 3,
    initial_question      TEXT NOT NULL,
    completion_conditions TEXT NOT NULL DEFAULT '[]', -- JSON 配列
    created_at            TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS missions_today (
    family_id  TEXT NOT NULL REFERENCES families (id) ON DELETE CASCADE,
    child_id   TEXT NOT NULL REFERENCES children (id) ON DELETE CASCADE,
    day        TEXT NOT NULL, -- YYYY-MM-DD
    mission_id TEXT NOT NULL REFERENCES missions (id),
    PRIMARY KEY (child_id, day)
);

CREATE TABLE IF NOT EXISTS sessions (
    id           TEXT PRIMARY KEY,
    child_id     TEXT NOT NULL REFERENCES children (id) ON DELETE CASCADE,
    mission_id   TEXT NOT NULL REFERENCES missions (id),
    character_id TEXT NOT NULL REFERENCES characters (id),
    state        TEXT NOT NULL, -- DialogueState (対話制御エンジンが管理。LLM に委譲しない)
    ended_at     TEXT,
    end_reason   TEXT, -- MissionEndReason のコード (6-4)
    started_at   TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_sessions_child ON sessions (child_id, started_at);

-- 会話履歴（テキストのみ。音声は破棄される）
CREATE TABLE IF NOT EXISTS messages (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL REFERENCES sessions (id) ON DELETE CASCADE,
    child_id   TEXT NOT NULL REFERENCES children (id) ON DELETE CASCADE, -- 家庭・子ども単位の分離を明示
    role       TEXT NOT NULL CHECK (role IN ('child', 'assistant', 'system')),
    content    TEXT NOT NULL,
    state      TEXT, -- そのやり取り時点の DialogueState
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_messages_session ON messages (session_id, id);
CREATE INDEX IF NOT EXISTS idx_messages_child ON messages (child_id, id);

CREATE TABLE IF NOT EXISTS mission_results (
    id               TEXT PRIMARY KEY,
    session_id       TEXT NOT NULL REFERENCES sessions (id) ON DELETE CASCADE,
    child_id         TEXT NOT NULL REFERENCES children (id) ON DELETE CASCADE,
    mission_id       TEXT NOT NULL REFERENCES missions (id),
    outcome          TEXT NOT NULL CHECK (outcome IN ('completed', 'child_exit', 'time_up', 'idle')),
    thinking_summary TEXT,
    signals          TEXT NOT NULL DEFAULT '{}', -- JSON (思考評価の内部状態スナップショット)
    finished_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS stamps (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    description TEXT NOT NULL,
    -- 獲得条件の判定コード。LLM の発言だけで付与せず、必ずアプリ側で検証する (05-safety-quality)
    criteria    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS child_stamps (
    child_id   TEXT NOT NULL REFERENCES children (id) ON DELETE CASCADE,
    stamp_id   TEXT NOT NULL REFERENCES stamps (id),
    session_id TEXT REFERENCES sessions (id) ON DELETE SET NULL,
    awarded_at TEXT NOT NULL DEFAULT (datetime('now')),
    -- 同一条件で何度も繰り返せない (02-features 7-1)
    PRIMARY KEY (child_id, stamp_id)
);

CREATE TABLE IF NOT EXISTS memories (
    id         TEXT PRIMARY KEY,
    child_id   TEXT NOT NULL REFERENCES children (id) ON DELETE CASCADE,
    type       TEXT NOT NULL CHECK (type IN
                 ('interest', 'knowledge', 'discovery', 'thinking', 'preference', 'open_question')),
    topic      TEXT,
    content    TEXT NOT NULL,
    source     TEXT, -- 根拠となるミッション/セッション。推測を確定知識として保存しない (02-features 8-3)
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_memories_child ON memories (child_id, created_at);

-- 思考記録（成功指標: 理由説明・仮説・別の可能性などの回数集計に使う）
CREATE TABLE IF NOT EXISTS thinking_records (
    id         TEXT PRIMARY KEY,
    child_id   TEXT NOT NULL REFERENCES children (id) ON DELETE CASCADE,
    session_id TEXT NOT NULL REFERENCES sessions (id) ON DELETE CASCADE,
    turn_index INTEGER NOT NULL,
    signals    TEXT NOT NULL DEFAULT '[]', -- JSON 配列 (explanation|hypothesis|alternatives|creative|...)
    summary    TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- 家庭・アプリ設定
CREATE TABLE IF NOT EXISTS app_settings (
    family_id  TEXT NOT NULL REFERENCES families (id) ON DELETE CASCADE,
    key        TEXT NOT NULL,
    value      TEXT NOT NULL, -- JSON
    PRIMARY KEY (family_id, key)
);

-- 利用時間の強制管理 (05-safety-quality: 利用時間はバックエンドで強制管理)
CREATE TABLE IF NOT EXISTS usage_daily (
    child_id     TEXT NOT NULL REFERENCES children (id) ON DELETE CASCADE,
    day          TEXT NOT NULL, -- YYYY-MM-DD (ローカル日付)
    used_seconds INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (child_id, day)
);
