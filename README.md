# CurioQuest

音声対話を通じて子どもの「自分で考える力」を育てる、家庭内ローカルAI知育プラットフォームのプロジェクトです。

本ブランチ（`agents/start-implementation-based-on-specs`）は、[docs/design/](docs/design/) の設計仕様書にもとづいて実装を進めるブランチです。設計仕様書・MVP案のまとまりは `voice-interactive-education-app-design` ブランチにあります。

## 基本コンセプト

> **毎日ちょっとした冒険をしながら、自分で考えることが好きになる**

AIは正解を提示することを主目的とせず、問いかけやヒントによって子ども自身の思考を促す。

## ドキュメントの読み方

設計仕様は [docs/design/](docs/design/) に分割して格納しています。初めての方はこの順に読むことを推奨します。

| 順 | ファイル | 内容 |
|---|---|---|
| 1 | [01-overview.md](docs/design/01-overview.md) | プロジェクト概要・基本コンセプト・設計原則・システム全体の機能一覧 |
| 2 | [02-features.md](docs/design/02-features.md) | サブシステム F01〜F07 の機能仕様（ユーザー管理・音声対話・キャラクター・ミッション・ゲーミフィケーション・Memory・保護者管理） |
| 3 | [03-architecture.md](docs/design/03-architecture.md) | 技術アーキテクチャ・データベース設計・API設計 |
| 4 | [04-mvp.md](docs/design/04-mvp.md) | MVPの実装範囲とユーザー体験（画面遷移） |
| 5 | [05-safety-quality.md](docs/design/05-safety-quality.md) | 安全性・品質要件と対話品質テスト |
| 6 | [06-roadmap.md](docs/design/06-roadmap.md) | 開発ロードマップ・受け入れ基準・成功指標・開発方針 |
| 7 | [07-first-milestone.md](docs/design/07-first-milestone.md) | 最初に実装する対象（音声対話エンジン）のタスク定義 |

## 機能サマリー

| ID | サブシステム | 主な機能 | MVP |
|---|---|---|---|
| F01 | ユーザー管理 | 子ども別アカウント・プロフィール | 必須 |
| F02 | 音声対話 | STT・LLM・TTS | 必須 |
| F03 | キャラクター | 性格・音声・対話スタイル | 必須 |
| F04 | ミッション | 問題生成・出題・進行管理 | 必須 |
| F05 | ゲーミフィケーション | スタンプ・称号・図鑑 | 一部 |
| F06 | Memory | 興味・過去の対話・思考記録 | 一部 |
| F07 | 保護者管理 | 利用状況・履歴・時間制限 | 必須 |

## 技術スタック（予定）

| 領域 | 推奨技術 | 用途 |
|---|---|---|
| フロントエンド | Streamlit（検証）/ React（製品） | 子ども向けUI・保護者画面 |
| バックエンド | Python / FastAPI | アプリケーションAPI |
| LLM | Ollama / Strata等（ローカル推論サーバー） | 対話・評価・ミッション生成 |
| LLM接続 | OpenAI互換APIアダプター | モデル切り替え |
| STT | faster-whisper | 音声認識 |
| TTS | VOICEVOX | キャラクター音声 |
| DB | SQLite（MVP）→ PostgreSQL（将来） | データ管理 |
| 音声処理 | Web Audio API | マイク・音声再生 |
| コンテナ | Docker Compose | サービス管理 |

## 開発状況

- [x] 設計仕様書・MVP案の作成（`voice-interactive-education-app-design` ブランチ）
- [x] 音声対話チェーンの実装（LLMアダプター・ルールエンジン・対話エンジン・STT/TTS・API一式・検証用デモページ）
- [ ] 実機（LOCAL LLM・VOICEVOX）を併せた end-to-end 検証
- [ ] 以降のフェーズ → [docs/design/06-roadmap.md](docs/design/06-roadmap.md) 参照

## 起動方法

### Docker Compose（Home PC 想定）

```bash
cp .env.example .env   # LLM URL など必要事項を編集（何も無くても起動は可能）
docker compose up --build
```

- 子アプリ: `http://localhost:8000/`（検証用デモページ。API は `/api/*`、API ドキュメントは `/docs`）
- LLM はデフォルトでホスト側の Ollama（`host.docker.internal:11434/v1`）を使う。LOCAL LLM（vLLM 等）に合わせて `.env` の `CURIOQUEST_LLM_BASE_URL` / `CURIOQUEST_LLM_MODEL` を設定する
- VOICEVOX Engine は compose 内で併走し、ホストには公開しない。STT（faster-whisper）はバックエンドプロセス内に組み込まれており、認識モデルは named volume にキャッシュする

### ローカル開発（Python 3.12 / uv）

```bash
uv sync --extra stt                  # 開発依存 + faster-whisper 込みの本番依存（STT なしなら `uv sync`）
.venv/bin/python -m pytest -q        # テスト実行
.venv/bin/uvicorn curioquest.api.main:create_standalone_app --factory --reload
# インストール後はコンソリスクリプトでも同じ起動ができる: curioquest-api
```

設定は全て `CURIOQUEST_*` 環境変数（対応表は [.env.example](.env.example) を参照）。LLM・VOICEVOX への接続は設定解決のみ起動時、実接続は初回リクエスト時に行うため、下準備が揃っていなくてもサーバー起動と会話のテキスト往復までなら検証できる。
