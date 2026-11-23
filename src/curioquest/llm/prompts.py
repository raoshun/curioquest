"""評価・ミッション生成用のシステムプロンプト組み立て。

ペルソナ対話とは分離し、評価処理は構造化出力だけを返させる。
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any

from curioquest.domain.models import MissionCategory

_EVALUATION_INSTRUCTION = """あなたは子どもの発話に含まれる思考の痕跡を分析する観察者です。
会話をよく読み、あてはまるものを下の一覧から選んで JSON で返してください。
判断に迷うものは選ばないでください。褒めたり励ましたりする必要はありません。

選べる思考の痕跡:
{signal_list}

追加で判断すること:
- shared_idea: 子どもが自分の考え・理由・アイデアを言葉にして共有できたかどうか

出力は次の形式の JSON オブジェクトのみとします:
{{"signals": ["選んだ code の配列"],
 "shared_idea": true または false,
 "summary": "観察の要約を1文で"}}"""

_MISSION_INSTRUCTION = """あなたは子どもの思考力を育てる
ミッション（お題）を 1 つデザインするゲームデザイナーです。
次の条件を守ってください。
- 小学生低学年でもわかるやさしい日本語にすること。
- 正解が 1 つに決まらない、考えを巡らせるお題にすること。
- 特定の知識がなくても自分の経験から考えられることにすること。

カテゴリは次から 1 つ選びます:
- curiosity: なぜなぜ探検（身近な「なぜ」を考える）
- reasoning: なぞとき探偵（手がかりから推理する）
- imagination: もしもワールド（想像して物語を広げる）

子どものプロファイルと過去の対話を参考にしながら、同じ内容の繰り返しにならないようにしてください。

出力は次の形式の JSON オブジェクトのみとします:
{"title": "子どもにも伝わる短いタイトル",
 "category": "curiosity または reasoning または imagination",
 "initial_question": "ミッションで子どもに最初に投げかける問い",
 "target_skills": ["observation などの skill code"],
 "estimated_minutes": 分数の整数}"""


def _signal_list_text() -> str:
    labels = {
        "explained_reason": "理由を説明した",
        "hypothesis": "仮説を立てた",
        "multiple_possibilities": "複数の可能性を考えた",
        "creative_idea": "独創的なアイデアを出した",
        "made_story": "自分の物語を作った",
        "used_hint_and_retry": "ヒントを使って再挑戦した",
        "asked_question": "自分から疑問を持った",
    }
    return "\n".join(f"- {code}: {label}" for code, label in labels.items())


def evaluation_system_prompt() -> str:
    """思考評価のシステムプロンプトを返す。"""
    return _EVALUATION_INSTRUCTION.format(signal_list=_signal_list_text())


def conversation_to_text(conversation: Sequence[Mapping[str, str]]) -> str:
    """会話シーケンスを評価用テキストにまとめる。role は child/assistant。"""
    lines = []
    for m in conversation:
        role = m.get("role", "user")
        lines.append(f"{role}: {m.get('content', '')}")
    return "\n".join(lines)


def mission_system_prompt() -> str:
    """ミッション生成のシステムプロンプトを返す。"""
    return _MISSION_INSTRUCTION


def mission_user_prompt(profile: Mapping[str, Any], history: Sequence[str] = ()) -> str:
    """ミッション生成用のユーザーメッセージを組み立てる。"""
    parts = ["子どものプロファイル:", json.dumps(dict(profile), ensure_ascii=False, indent=2)]
    if history:
        parts.append("")
        parts.append("過去のミッションや出来事:")
        parts.extend(f"- {item}" for item in history)
    else:
        parts.append("過去の対話はまだありません。")
    parts.append("")
    parts.append("ミッションを 1 つ生成してください。")
    return "\n".join(parts)


def parse_mission_category(value: Any) -> MissionCategory:
    """文字列をカテゴリへ変換する。未知の値は curiosity に倒す。"""
    try:
        return MissionCategory(str(value))
    except ValueError:
        return MissionCategory.CURIOSITY
