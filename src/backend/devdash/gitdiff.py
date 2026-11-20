"""git 이 계산한 변경(diff)을 화면이 좌우로 펼치기 좋은 모양으로.

비교는 git 이 한 그대로 쓴다 — 브라우저에서 다시 계산하면 git log · GitHub 와 결과가 달라질 수 있다.
여기서는 `git diff` 출력의 변경 덩어리(hunk)만 읽어 넘긴다.
이전 · 이후를 짝짓고 줄 번호를 매기는 일은 화면이 한다 (그리는 방식에 딸린 일이라).

    @@ -12,4 +12,5 @@ def foo():      ← 덩어리 머리: 이전 12번째 줄부터 4줄, 이후 12번째 줄부터 5줄
     같은 줄                          ← ' ' 양쪽 같음
    -지운 줄                          ← '-' 이전에만
    +더한 줄                          ← '+' 이후에만
    \\ No newline at end of file       ← 파일 끝 줄바꿈 표시. 화면에는 필요 없다
"""

from __future__ import annotations

import re

HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


def parse(patch: str) -> dict:
    """한 파일의 `git diff` 출력 →
    {"binary": bool, "hunks": [{oldStart, oldLines, newStart, newLines, lines}]}

    lines 는 [종류, 글] 목록이고 종류는 ' ' · '-' · '+' 중 하나다.
    """
    hunks: list[dict] = []
    binary = False
    current: dict | None = None
    old_left = new_left = 0  # 지금 덩어리에서 아직 읽을 이전 · 이후 줄 수
    for line in patch.split("\n"):
        if current is not None and (old_left > 0 or new_left > 0):
            if line == "" and old_left > 0 and new_left > 0:
                line = " "  # diff.suppressBlankEmpty 를 켜면 같은 빈 줄이 앞 공백 없이 온다
            line = line.removesuffix("\r")  # CRLF 파일의 줄 끝 (화면에 보일 글자가 아니다)
            kind = line[:1]
            if kind == " " and old_left > 0 and new_left > 0:
                old_left, new_left = old_left - 1, new_left - 1
            elif kind == "-" and old_left > 0:
                old_left -= 1
            elif kind == "+" and new_left > 0:
                new_left -= 1
            elif kind == "\\":
                continue  # \ No newline at end of file
            else:
                current = None  # 머리에 적힌 줄 수와 어긋난다. 여기서 덩어리를 닫는다
                continue
            current["lines"].append([kind, line[1:]])
            continue
        if m := HUNK.match(line):
            old_start, old_len, new_start, new_len = m.groups()
            # ',길이'가 없으면 1줄이다 (git diff 형식)
            old_left = int(old_len) if old_len is not None else 1
            new_left = int(new_len) if new_len is not None else 1
            current = {
                "oldStart": int(old_start),
                "oldLines": old_left,
                "newStart": int(new_start),
                "newLines": new_left,
                "lines": [],
            }
            hunks.append(current)
        elif line.startswith("Binary files ") or line.startswith("GIT binary patch"):
            # 글이 아닌 파일은 덩어리 대신 이 한 줄뿐이다
            binary = True
        # 그 밖(diff --git · index · --- · +++ · 덩어리 뒤의 \ No newline …)은 머리줄이라 건너뛴다
    return {"binary": binary, "hunks": hunks}
