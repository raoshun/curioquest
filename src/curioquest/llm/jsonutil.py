"""LLM 応答からの JSON 抽出ユーティリティ。

ローカルモデルは JSON 以外にコードフェンスや前置きを付与することがあるため、
最初の JSON オブジェクトの範囲を頑健に抜き出す。
"""

from __future__ import annotations

import json
import re
from typing import Any

_FENCE_RE = re.compile(r"```(?:json)?\s*(.+?)\s*```", re.DOTALL)


def extract_json_object(text: str) -> dict[str, Any]:
    """テキストから最初の JSON オブジェクトを取り出して返す。

    Raises:
        ValueError: JSON オブジェクトが見つからない、または不正な場合。
    """
    candidates: list[str] = []
    fenced = _FENCE_RE.search(text)
    if fenced:
        candidates.append(fenced.group(1))
    candidates.append(text)
    for candidate in candidates:
        start = candidate.find("{")
        if start == -1:
            continue
        # 対応する閉じブレースを探索する
        depth = 0
        in_string = False
        escaped = False
        for i in range(start, len(candidate)):
            ch = candidate[i]
            if in_string:
                if escaped:
                    escaped = False
                elif ch == "\\":
                    escaped = True
                elif ch == '"':
                    in_string = False
            elif ch == '"':
                in_string = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    try:
                        parsed = json.loads(candidate[start : i + 1])
                    except json.JSONDecodeError:
                        break
                    if isinstance(parsed, dict):
                        return parsed
                    break
    raise ValueError(f"JSON オブジェクトを抽出できません: {text!r}")
