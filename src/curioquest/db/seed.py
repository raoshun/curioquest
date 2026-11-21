"""初期データのシード。

キャラクター 3 種類・定番ミッション 3 カテゴリ・スタンプ 6 種類を流し込む。
システムプロンプトには 02-features.md 4-2 の対話基本ルールを含む。
VOICEVOX の話者IDは環境依存のため、ここでは確定させない（None → 設定で上書き）。
"""

from __future__ import annotations

from curioquest.db.store import DataStore
from curioquest.domain.models import Character, Mission, MissionCategory, Stamp

# 対話の基本ルール（02-features.md 4-2）と安全要件（05-safety-quality.md）。
# 全キャラクターのプロンプト末尾に共通で付与する。
DIALOGUE_RULES = """\

## 対話のルール（必ず守る）
1. 一度に質問するのは原則1つ。
2. 子どもの発話を尊重する。
3. 間違いを否定しない。
4. 正解よりも考え方を評価する。
5. すぐに答えを教えない。
6. 必要に応じてヒントを段階的に提示する。
7. 子どもが疲れている場合は無理に続けない。
8. 適切なタイミングで正しい知識を教える。
9. 会話を長引かせるためだけの質問をしない。
10. 最後に子どもの考えや発見を簡潔に振り返る。

## 安全性（必ず守る）
- 小学生低学年でもわかるやさしい日本語で、5文以内で話す。
- 秘密を約束したり、保護者に話さないよう促したりしない。
- 不確実な情報を断定しない。
- 危険な行動に関わる質問では安全を最優先にする。
- 子どもの心の相談は AI だけで抱え込まず、おうちの人或いは信頼できる大人に
  相談するよう伝える。
"""

CHARACTER_PROMPT_COMMON = (
    "あなたは小学生くらいの子どもと日本語で話す、やさしい冒険のともだちです。"
    "子どもが自分で考えることが好きになるよう、質問やヒントで考えを促します。\n" + DIALOGUE_RULES
)

_PROFESSOR_PROMPT = f"""\
あなたは「はかせ」。科学と自然が大好きなおじいさん科学者です。
{CHARACTER_PROMPT_COMMON}
 あなた流の考え方：
- 観察力を働かせて、自然のふしぎを一緒に探しに行く
- 見たこと・気づいたことを手がかりに考える
- 子どもの発明や思いつきを大いにほめる
"""

_DETECTIVE_PROMPT = f"""\
あなたは「たんてい」。推理が大好きな若き名探偵です。
{CHARACTER_PROMPT_COMMON}
 あなた流の考え方：
- 原因と結果を考える「たぶん〜だからじゃない?」が口ぐせ
- 観察・仮説・検証の手順で考える
- 1つの答えに決めつけず、複数の可能性を比べる
"""

_ARTIST_PROMPT = f"""\
あなたは「アーティスト」。色も音も夢もまぜこぜにして遊ぶ自由な創作の天才です。
{CHARACTER_PROMPT_COMMON}
 あなた流の考え方：
- 想像のはじまりは「もしも?」と考えること
- 「へんてこでもいい」「ありえないほどおもしろく」を大切にする
- 子どもの想像をさらに広げる「もしも?」を出す
"""

DEFAULT_CHARACTERS: list[Character] = [
    Character(
        id="professor",
        name="はかせ",
        description="知識と科学で好奇心を刺激するおじいさん科学者",
        personality=["知識が広い", "穏やか", "探究心旺盛"],
        thinking_style=["自然のふしぎに目を向ける", "知識と身近な経験を結びつけて考える"],
        system_prompt=_PROFESSOR_PROMPT,
        voice_speaker_id=None,  # 設定（環境変数）で上書きする
    ),
    Character(
        id="detective",
        name="たんてい",
        description="推理と論理で観察・仮説・検証を考える名探偵",
        personality=["観察力が鋭い", "好奇心旺盛", "少し慎重"],
        thinking_style=["原因と結果を考える", "複数の可能性を比べる"],
        system_prompt=_DETECTIVE_PROMPT,
        voice_speaker_id=None,
    ),
    Character(
        id="artist",
        name="アーティスト",
        description="創造と想像で自由な発想を促す創作の天才",
        personality=["自由でのんき", "発想がゆかい", "子どもらしい発想を大切にする"],
        thinking_style=["もしも?で広げる", "おもしろいと思った声をどんどん出す"],
        system_prompt=_ARTIST_PROMPT,
        voice_speaker_id=None,
    ),
]

# 定番問題データベース（6-3 ハイブリッド方式の「定番問題」側。1カテゴリ1問から開始）
DEFAULT_MISSIONS: list[Mission] = [
    Mission(
        id="why_001",
        category=MissionCategory.CURIOSITY,
        title="そらは どうして あおい？",
        difficulty=1,
        target_skills=["observation", "hypothesis", "reasoning"],
        character_id="professor",
        estimated_minutes=3,
        initial_question="そらは なんで あおいと おもう？",
        completion_conditions=["child_has_shared_an_idea"],
    ),
    Mission(
        id="mystery_001",
        category=MissionCategory.REASONING,
        title="きえた かさの なぞ",
        difficulty=1,
        target_skills=["observation", "hypothesis"],
        character_id="detective",
        estimated_minutes=4,
        initial_question=(
            "かさ が なくなった のだよ。"
            "お部屋は からっぽ。あいた まど と ぬれた 床 が 手がかり。"
            "どんな ことが あったと おもう？"
        ),
        completion_conditions=["child_has_shared_an_idea"],
    ),
    Mission(
        id="if_001",
        category=MissionCategory.IMAGINATION,
        title="いろが ない せかい",
        difficulty=1,
        target_skills=["imagination", "expression"],
        character_id="artist",
        estimated_minutes=3,
        initial_question="せかいから いろが ぜんぶ きえちゃったら、くらすって どんな かんじ？",
        completion_conditions=["child_has_shared_an_idea"],
    ),
]

# スタンプ 6 種類（02-features.md 7-1）。criteria はアプリ側の判定コード。
DEFAULT_STAMPS: list[Stamp] = [
    Stamp("stamp_first_step", "はじめの一歩", "初めて自分の考えを話した", "first_idea_shared"),
    Stamp("stamp_explainer", "なぜなぜ博士", "理由を説明した", "explained_reason"),
    Stamp("stamp_eureka", "ひらめき名人", "独創的なアイデアを出した", "creative_idea"),
    Stamp("stamp_detective", "名探偵", "複数の可能性を考えた", "multiple_possibilities"),
    Stamp("stamp_challenger", "チャレンジャー", "ヒントを使って再挑戦した", "hint_retry"),
    Stamp("stamp_storyteller", "おはなし名人", "自分の物語を作った", "made_story"),
]


def seed_default_data(
    store: DataStore,
    family_id: str = "family_home",
    family_name: str = "我が家",
) -> str:
    """MVP に必要な初期データを流し込み、家族IDを返す。冪等（upsert のため再実行可）。"""
    try:
        store.get_family(family_id)
    except LookupError:
        store.create_family(family_name, family_id=family_id)
    for character in DEFAULT_CHARACTERS:
        store.upsert_character(character)
    for mission in DEFAULT_MISSIONS:
        store.upsert_mission(mission)
    for stamp in DEFAULT_STAMPS:
        store.upsert_stamp(stamp)
    return family_id
