"""git diff 출력 읽기. 화면이 좌우로 펼칠 변경 덩어리만 뽑는다."""

from __future__ import annotations

from devdash import gitdiff

PATCH = """diff --git a/a.py b/a.py
index 1111111..2222222 100644
--- a/a.py
+++ b/a.py
@@ -1,3 +1,4 @@ def foo():
 같음
-지움
+더함 1
+더함 2
 끝
@@ -10 +11 @@
-한 줄
+바꾼 줄
\\ No newline at end of file
"""


def test_덩어리마다_시작_줄과_줄_수와_종류별_글을_준다():
    out = gitdiff.parse(PATCH)
    assert out["binary"] is False
    first, second = out["hunks"]
    assert (first["oldStart"], first["oldLines"], first["newStart"], first["newLines"]) == (
        1,
        3,
        1,
        4,
    )
    assert first["lines"] == [
        [" ", "같음"],
        ["-", "지움"],
        ["+", "더함 1"],
        ["+", "더함 2"],
        [" ", "끝"],
    ]
    # ',길이'가 없으면 1줄, 파일 끝 줄바꿈 표시는 빠진다
    assert (second["oldLines"], second["newLines"]) == (1, 1)
    assert second["lines"] == [["-", "한 줄"], ["+", "바꾼 줄"]]


def test_빈_줄과_다음_파일_머리줄을_헷갈리지_않는다():
    patch = "@@ -1,2 +1,2 @@\n \n-a\n+b\ndiff --git a/x b/x\n--- a/x\n+++ b/x\n"
    (hunk,) = gitdiff.parse(patch)["hunks"]
    # 빈 같은 줄(' ')은 넣고, 머리에 적힌 줄 수를 다 읽은 뒤의 '--- a/x' 는 지운 줄이 아니다
    assert hunk["lines"] == [[" ", ""], ["-", "a"], ["+", "b"]]


def test_줄_안의_CR_과_앞_공백_없는_빈_줄에도_덩어리가_잘리지_않는다():
    """글 모드로 읽으면 CR 하나가 줄바꿈이 되어 덩어리가 중간에서 끊겼다."""
    patch = "@@ -1,4 +1,4 @@\n a\r\n\n-b\rX\n+c\n d\n"
    (hunk,) = gitdiff.parse(patch)["hunks"]
    assert hunk["lines"] == [[" ", "a"], [" ", ""], ["-", "b\rX"], ["+", "c"], [" ", "d"]]


def test_글이_아닌_파일은_binary():
    out = gitdiff.parse("diff --git a/i.png b/i.png\nBinary files a/i.png and b/i.png differ\n")
    assert out == {"binary": True, "hunks": []}
