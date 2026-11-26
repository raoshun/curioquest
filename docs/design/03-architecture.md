# 10. 技術アーキテクチャ

## 10-1. 推奨技術スタック

既存のローカルAI環境との親和性を考慮し、以下を推奨する。

| 領域 | 推奨技術 | 用途 |
|---|---|---|
| フロントエンド | Streamlit または React | 子ども向けUI・保護者画面 |
| バックエンド | Python / FastAPI | アプリケーションAPI |
| LLM | Ollama / Strata等 | ローカル推論 |
| LLM接続 | OpenAI互換APIアダプター | モデル切り替え |
| STT | faster-whisper | 音声認識 |
| TTS | VOICEVOX | キャラクター音声 |
| DB | SQLite | MVPのデータ管理 |
| 将来のDB | PostgreSQL | 複数端末・データ拡張 |
| 音声処理 | Web Audio API | マイク・音声再生 |
| コンテナ | Docker Compose | サービス管理 |

### フロントエンドについて

最終的にはReactを推奨する。理由は以下のとおり。

- 子ども向けの直感的な画面を作りやすい。
- キャラクターのアニメーションを追加しやすい。
- 音声の再生・録音・割り込み制御を細かく実装できる。
- タブレットやスマートフォンに対応しやすい。

ただし、最初の技術検証ではStreamlitでも構わない。

重要なのは、バックエンドとフロントエンドを分離し、将来のUI変更でAI基盤を作り直さなくて済むようにすることである。

## 10-2. コンテナ構成

```text
Docker Compose
│
├── frontend
│    └── React
│
├── backend
│    └── FastAPI
│
├── llm-adapter
│    └── Ollama / Strata 接続
│
├── stt
│    └── faster-whisper
│
├── voicevox
│    └── VOICEVOX Engine
│
└── database
     └── SQLite (MVP)
```

LLMは既存の推論サーバーを利用し、アプリケーション用コンテナにGPUを直接割り当てる必要はない。
これにより、現在のローカルLLM環境を活用できる。

## 10-3. LLMの抽象化

モデルに依存しない設計にする。

```python
class LLMProvider:

    def generate_response(self, messages, schema=None):
        pass

    def evaluate_thinking(self, conversation):
        pass

    def generate_mission(self, profile, history):
        pass
```

実際には、各機能を別インターフェースに分割しても構わない。
特に重要なのは、通常対話と評価処理を分離することである。

例えば、対話には高速なモデルを使い、思考評価には少し高性能なモデルを使う構成も可能である。
ただし、MVPではモデルを増やしすぎず、まずは単一モデルで成立するかを検証する。

---

# 11. データベース設計

MVPで必要な主要テーブルを定義する。

| テーブル | 主な用途 |
|---|---|
| families | 家庭情報 |
| children | 子どもアカウント |
| characters | キャラクター定義 |
| missions | ミッション定義 |
| sessions | 対話セッション |
| messages | 会話履歴 |
| mission_results | ミッション結果 |
| stamps | スタンプ定義 |
| child_stamps | 獲得スタンプ |
| memories | 長期記憶 |
| thinking_records | 思考記録 |
| app_settings | 家庭・アプリ設定 |

## データ設計上の重要事項

- すべての子ども固有データには `child_id` を持たせる。大人アカウントも同じ `child_id` 列を共通識別子として利用する。
- 全ユーザーは登録時に生年月日（`birthday`）を必須とする。年齢は保存せず、`birthday` から対話時に計算して会話レベルの参考材料として利用する。
- 家庭単位のデータには `family_id` を持たせる。
  これにより、将来複数家庭への提供に発展させる場合も拡張しやすくなる。
- 音声データについては、MVPでは原則保存しない。STTでテキスト化した後、音声データは破棄する設計を基本とする。
- 会話履歴も保護者が削除できるようにする。

---

# 12. API設計

フロントエンドとバックエンドを分離するため、REST APIを基本とする。

| Method | Endpoint | 用途 |
|---|---|---|
| GET | /api/children | ユーザー（子ども・大人）一覧 |
| POST | /api/children | ユーザー登録（生年月日必須） |
| GET | /api/children/{id} | プロフィール取得 |
| PATCH | /api/children/{id} | プロフィール更新 |
| GET | /api/characters | キャラクター一覧 |
| GET | /api/missions/today | 今日のミッション |
| POST | /api/sessions | 対話開始 |
| POST | /api/sessions/{id}/message | 発話送信 |
| POST | /api/sessions/{id}/complete | ミッション終了 |
| GET | /api/children/{id}/stamps | スタンプ一覧 |
| GET | /api/children/{id}/memories | Memory取得 |
| GET | /api/parent/dashboard | 保護者ダッシュボード |

音声認識については、フロントエンドから音声を送信するAPIを別途用意する。
長時間の音声接続が必要になった場合は、WebSocketへの移行も検討する。
定型文を組み合わせた対話では合成音の再利用が可能なため、音声キャッシュによる
レスポンス性改善は 08-voice-cache.md で検討する。
