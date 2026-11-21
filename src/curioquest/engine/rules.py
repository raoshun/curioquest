"""対話エンジンの決定論的ルール（キーワード判定・定数・対応表）。

LLM に状態遷移や安全判定を委ねないため、終了・ヒント・危険話題などの
切り分けはここでキーワード照合により行う（02-features.md 4-2 / 7-1）。
STT 誤変換を考慮し、誤検出の少ない複合語のみをキーに使う。
"""

from __future__ import annotations

from enum import StrEnum

from curioquest.domain.models import ThinkingSignal


class ChildIntent(StrEnum):
    """子ども発話の意図（02-features.md 4-2）。"""

    EXIT = "exit"  # ミッション終わり
    ASK_ANSWER = "ask_answer"  # 答えやヒントを求める
    DONT_KNOW = "dont_know"  # 分からない・難しい
    DANGEROUS = "dangerous"  # 一人ではいけない危険な話題
    NORMAL = "normal"  # それ以外の発話


# 終了意図: 単独の「もういい」は誤検出リスクが高いので複合語のみ
_EXIT_KEYWORDS: tuple[str, ...] = (
    "おしまい",
    "終わりたい",
    "終わりにしたい",
    "おわりにしたい",
    "やめたい",
    "やめます",
    "やめる",
    "おわり",
)

# 答え・ヒント要求
_ASK_ANSWER_KEYWORDS: tuple[str, ...] = (
    "おしえて",
    "教えて",
    "しりたい",
    "知りたい",
    "答え",
    "こたえ",
    "なんですか",
    "なんだっけ",
    "ほうこくする",
    "報告する",
)

# 分からない・難しい
_DONT_KNOW_KEYWORDS: tuple[str, ...] = (
    "わからない",
    "わかんない",
    "分からん",
    "分からない",
    "むずかしい",
    "難しい",
    "むつかしい",
    "できない",
    "思いつかない",
    "おもいつかない",
)

# 危険話題（7-1）。STT 誤変換での誤検出を避けるため複合語のみ。
# 例: 「しょうぼうしゃ」単独ではなく「火遊び」など具体的な行動語を見る。
_DANGEROUS_KEYWORDS: tuple[str, ...] = (
    "火遊び",
    "ひあそび",
    "火をつけます",
    "火をつけて",
    "火をつける",
    "マッチで",
    "マッチあそび",
    "ぼやをおこす",
    "包丁で",
    "ナイフで",
    "カッターで",
    "刃物",
    "コンセントに",
    "コンセントさす",
    "感電",
    "ベランダからおりる",
    "ベランダのした",
    "屋根の上にのぼ",
    "線路にいる",
    "線路にはい",
    "立ち入り禁止",
    "こおった池",
    "こおったいけ",
    "こおりであそぶ",
    "がけからおりる",
    "がけのふち",
)

# 危険話題と判定したときの固定的な応答（LLM は呼ばない、02-features.md 7-1）
SAFETY_REPLY = (
    "ちょっと待って。それはこわいかもしれないから、ひとりでは絶対にやらないでね。"
    "火やとがったもの、コンセントまわりのあそびは、おとなといっしょじゃないと大けがしてしまうんだ。"
    "今やろうとしていることや、見たことを、近くにいるおとなにすぐに教えてもらえる？"
    "つぎに考えるあそびをしよう。さっきはなにがしりたいだったけ？"
)

# 思考信号 -> スタンプレター名（02-features.md 7-2）
SIGNAL_TO_CRITERIA: dict[ThinkingSignal, str] = {
    ThinkingSignal.EXPLAINED_REASON: "explained_reason",
    ThinkingSignal.USED_HINT_AND_RETRY: "hint_retry",
    ThinkingSignal.MULTIPLE_POSSIBILITIES: "multiple_possibilities",
    ThinkingSignal.CREATIVE_IDEA: "creative_idea",
    ThinkingSignal.MADE_STORY: "made_story",
}

# 「はじめてのアイデア共有」スタンプは共有完了時に付与する
FIRST_IDEA_CRITERION = "first_idea_shared"

# ミッション完了条件文字列（models.Mission.completion_conditions に格納）
SHARED_IDEA_CONDITION = "child_has_shared_an_idea"


def detect_intent(text: str) -> ChildIntent:
    """発話テキストから意図を検出する。

    優先順位: dangerous > exit > ask_answer > dont_know > normal。
    空文字は normal（親側で空発話として別途扱う）。
    """
    stripped = text.strip()
    if not stripped:
        return ChildIntent.NORMAL
    if any(k in stripped for k in _DANGEROUS_KEYWORDS):
        return ChildIntent.DANGEROUS
    if any(k in stripped for k in _EXIT_KEYWORDS):
        return ChildIntent.EXIT
    if any(k in stripped for k in _ASK_ANSWER_KEYWORDS):
        return ChildIntent.ASK_ANSWER
    if any(k in stripped for k in _DONT_KNOW_KEYWORDS):
        return ChildIntent.DONT_KNOW
    return ChildIntent.NORMAL


def is_asking_for_help(intent: ChildIntent) -> bool:
    """答え・ヒントを求めている意図なら True。"""
    return intent in (ChildIntent.ASK_ANSWER, ChildIntent.DONT_KNOW)
