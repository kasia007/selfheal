"""diff 를 git diff 스타일 HTML 로 렌더링합니다. (``--open-diff`` 전용)

``report.py`` 는 exit code 상수·``Console``(마스킹 단일 지점)·``write_artifacts`` 로
책임이 좁혀져 있습니다(README §8). 브라우저로 보여줄 HTML을 만드는 일은 그 책임이
아니라서 여기 별도 모듈로 둡니다. 입력은 ``write_artifacts`` 가 이미 만든 unified
diff 문자열을 그대로 재사용하고, ``make_diff`` 를 다시 부르지 않습니다.
"""

from __future__ import annotations

import re
from html import escape

_HUNK_RE = re.compile(r"^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@")

_STYLE = """
  :root {
    --bg: #f6f8fa; --surface: #ffffff; --border: #d0d7de; --border-soft: #e4e9ee;
    --text: #1f2428; --text-muted: #636c76;
    --add-bg: #dafbe1; --add-text: #116329;
    --del-bg: #ffebe9; --del-text: #a40e26;
    --hunk-bg: #ddf4ff; --hunk-text: #0969da; --gutter-text: #8c959f;
    --highlight-bg: #fff3c4; --highlight-border: #9a6700;
  }
  @media (prefers-color-scheme: dark) {
    :root:not([data-theme="light"]) {
      --bg: #0d1117; --surface: #12161c; --border: #2d333b; --border-soft: #22272e;
      --text: #e6edf3; --text-muted: #8b949e;
      --add-bg: #122117; --add-text: #56d364;
      --del-bg: #2a1315; --del-text: #ff7b72;
      --hunk-bg: #0f2231; --hunk-text: #6cb6ff; --gutter-text: #636c76;
      --highlight-bg: #3b2f05; --highlight-border: #d29922;
    }
  }
  :root[data-theme="dark"] {
    --bg: #0d1117; --surface: #12161c; --border: #2d333b; --border-soft: #22272e;
    --text: #e6edf3; --text-muted: #8b949e;
    --add-bg: #122117; --add-text: #56d364;
    --del-bg: #2a1315; --del-text: #ff7b72;
    --hunk-bg: #0f2231; --hunk-text: #6cb6ff; --gutter-text: #636c76;
    --highlight-bg: #3b2f05; --highlight-border: #d29922;
  }
  * { box-sizing: border-box; }
  body { margin: 0; background: var(--bg); color: var(--text);
         font-family: -apple-system, "Segoe UI", sans-serif; padding: 32px 16px; }
  .page { max-width: 860px; margin: 0 auto; }
  h1 { font-size: 18px; margin: 0 0 18px; }
  .file-card { background: var(--surface); border: 1px solid var(--border);
               border-radius: 8px; overflow: hidden; margin-bottom: 18px; }
  .file-header { padding: 10px 14px; border-bottom: 1px solid var(--border);
                 font-family: ui-monospace, "Consolas", monospace; font-size: 13px;
                 font-weight: 600; }
  .diff-scroll { overflow-x: auto; }
  table.diff { border-collapse: collapse; width: 100%;
               font-family: ui-monospace, "Consolas", monospace;
               font-size: 12.5px; line-height: 20px; }
  table.diff td { padding: 0; vertical-align: top; white-space: pre; }
  .gutter { width: 1%; min-width: 42px; padding: 0 10px !important; text-align: right;
            color: var(--gutter-text); border-right: 1px solid var(--border-soft); }
  .marker { width: 1%; min-width: 20px; text-align: center; font-weight: 600; }
  .code { width: 100%; padding: 0 16px 0 10px !important; }
  tr.add .gutter, tr.add .marker, tr.add .code { background: var(--add-bg); }
  tr.add .marker { color: var(--add-text); }
  tr.del .gutter, tr.del .marker, tr.del .code { background: var(--del-bg); }
  tr.del .marker { color: var(--del-text); }
  tr.hunk td { background: var(--hunk-bg); color: var(--hunk-text); font-weight: 500;
               padding: 3px 0 !important; }
  tr.hunk .gutter { border-right: none; }
  tr.highlight .gutter, tr.highlight .code { background: var(--highlight-bg); }
  tr.highlight .gutter { border-left: 3px solid var(--highlight-border); font-weight: 700; }
"""


def _split_files(diff: str) -> list[tuple[str, list[str]]]:
    """``--- a/...`` 경계로 diff 를 파일 단위로 나눕니다."""
    files: list[tuple[str, list[str]]] = []
    name = ""
    body: list[str] = []
    for line in diff.splitlines():
        if line.startswith("--- "):
            if name or body:
                files.append((name, body))
            body = []
            name = ""
        elif line.startswith("+++ "):
            name = line[4:].removeprefix("b/")
        else:
            body.append(line)
    if name or body:
        files.append((name, body))
    return files


def _render_rows(body: list[str]) -> str:
    rows = []
    old_line = new_line = 0
    for line in body:
        if not line:
            continue
        if line.startswith("@@"):
            m = _HUNK_RE.match(line)
            if m:
                old_line, new_line = int(m.group(1)), int(m.group(2))
            rows.append(
                f'<tr class="hunk"><td class="gutter"></td><td class="gutter"></td>'
                f'<td class="marker"></td><td class="code">{escape(line)}</td></tr>'
            )
            continue
        kind, content = line[0], line[1:]
        if kind == "+":
            rows.append(
                f'<tr class="add"><td class="gutter"></td><td class="gutter">{new_line}</td>'
                f'<td class="marker">+</td><td class="code">{escape(content)}</td></tr>'
            )
            new_line += 1
        elif kind == "-":
            rows.append(
                f'<tr class="del"><td class="gutter">{old_line}</td><td class="gutter"></td>'
                f'<td class="marker">&minus;</td><td class="code">{escape(content)}</td></tr>'
            )
            old_line += 1
        else:
            rows.append(
                f'<tr class="ctx"><td class="gutter">{old_line}</td><td class="gutter">{new_line}</td>'
                f'<td class="marker"></td><td class="code">{escape(content)}</td></tr>'
            )
            old_line += 1
            new_line += 1
    return "".join(rows)


def render_diff_html(diff: str) -> str:
    """unified diff 문자열을 받아 색이 입혀진 정적 HTML 문서를 돌려줍니다.

    ``--- a/파일`` 경계마다 파일별 카드로 나누고, ``+``/``-``/``@@`` 줄을 각각
    초록·빨강·파랑으로 구분합니다. 오프라인 로컬 파일로 여는 것을 전제로 외부
    CDN(폰트 등)은 쓰지 않고 시스템 monospace 폰트만 씁니다.
    """
    cards = []
    for name, body in _split_files(diff):
        cards.append(
            f'<div class="file-card"><div class="file-header">{escape(name)}</div>'
            f'<div class="diff-scroll"><table class="diff">{_render_rows(body)}</table>'
            f"</div></div>"
        )
    return (
        "<!doctype html><html><head><meta charset=\"utf-8\">"
        "<title>Patch Diff</title>"
        f"<style>{_STYLE}</style></head><body><div class=\"page\">"
        "<h1>검수용 패치 (exit 4 · 승인 대기)</h1>"
        f"{''.join(cards)}</div></body></html>"
    )


_SUGGESTION_STYLE = """
  .notice-card { background: var(--surface); border: 1px solid var(--border);
                 border-radius: 8px; padding: 20px 22px; }
  .notice-card p.lead { margin: 0 0 16px; color: var(--text-muted); font-size: 13px; }
  .notice-body { line-height: 1.65; font-size: 14px;
                 font-family: -apple-system, "Segoe UI", sans-serif; }
  .notice-body h3.suggestion-heading { margin: 20px 0 6px; font-size: 14px;
                 color: var(--hunk-text); }
  .notice-body h3.suggestion-heading:first-child { margin-top: 0; }
  .notice-body p { margin: 0 0 10px; }
  .notice-body table.suggestion-code { margin: 4px 0 16px; display: block;
                 max-width: 100%; overflow-x: auto; background: var(--hunk-bg);
                 border-radius: 6px; }
  table.attempts { border-collapse: collapse; width: 100%; font-size: 13px; }
  table.attempts th, table.attempts td { text-align: left; padding: 8px 10px;
                 border-bottom: 1px solid var(--border-soft); vertical-align: top; }
  table.attempts th { color: var(--text-muted); font-weight: 600; font-size: 12px;
                 white-space: nowrap; }
  table.attempts td.index { font-weight: 700; font-variant-numeric: tabular-nums;
                 white-space: nowrap; width: 1%; }
  table.attempts td.kind { white-space: nowrap; width: 1%; }
  table.attempts td.summary { font-family: ui-monospace, "Consolas", monospace;
                 font-size: 12.5px; font-weight: 600; }
  .kind-badge { display: inline-block; padding: 2px 9px; border-radius: 999px;
                 font-weight: 700; font-size: 12px; }
  .kind-badge.build { background: var(--del-bg); color: var(--del-text); }
  .kind-badge.test { background: var(--highlight-bg); color: var(--highlight-border); }
  .kind-badge.passed { background: var(--add-bg); color: var(--add-text); }
  .test-badge { font-size: 11px; font-weight: 700; color: var(--text-muted);
                 text-transform: uppercase; letter-spacing: 0.03em; }
  details.file-card summary { cursor: pointer; padding: 10px 14px;
                 font-family: ui-monospace, "Consolas", monospace; font-size: 13px;
                 font-weight: 600; list-style: none; }
  details.file-card summary::-webkit-details-marker { display: none; }
  details.file-card summary::before { content: "▸ "; color: var(--text-muted); }
  details.file-card[open] summary::before { content: "▾ "; }
  details.file-card summary { border-bottom: 1px solid transparent; }
  details.file-card[open] summary { border-bottom: 1px solid var(--border); }
"""


_FENCE_RE = re.compile(r"```[\w-]*\n(.*?)```", re.DOTALL)

# LLM 이 "**21번째 줄**", "줄 21", "line 21" 등 표현을 섞어 쓰므로 형식을 하나로
# 강제하는 대신 흔한 표현을 전부 잡습니다. 못 잡아도 본문 자체는 그대로 보여주니
# (아래 참고 코드만 못 켜질 뿐) 안전한 하위 호환입니다.
_LINE_REF_RE = re.compile(r"(\d+)\s*번째\s*줄|줄\s*(\d+)|line\s*(\d+)", re.IGNORECASE)


def _extract_referenced_lines(body: str) -> set[int]:
    """제안 텍스트에서 언급된 줄 번호를 전부 뽑습니다."""
    lines: set[int] = set()
    for match in _LINE_REF_RE.finditer(body):
        for group in match.groups():
            if group:
                lines.add(int(group))
                break
    return lines


def _render_source_with_highlights(source: str, highlighted: set[int]) -> str:
    """소스 전체를 줄 번호와 함께 보여주고, 제안이 언급한 줄만 강조합니다."""
    rows = []
    for i, line in enumerate(source.splitlines(), start=1):
        cls = "highlight" if i in highlighted else "ctx"
        rows.append(
            f'<tr class="{cls}"><td class="gutter">{i}</td>'
            f'<td class="marker">{"▶" if i in highlighted else ""}</td>'
            f'<td class="code">{escape(line)}</td></tr>'
        )
    return "".join(rows)


def _render_prose(text: str) -> str:
    """제안 텍스트 중 코드가 아닌 부분을 문단·소제목으로 나눕니다.

    ``### 줄 N`` 처럼 쓰라고 프롬프트에서 요청한 소제목만 따로 강조하고,
    나머지는 그냥 문단입니다 — 완전한 마크다운을 구현하는 게 아니라, 코드와
    설명을 구분해서 보여주는 최소한의 처리입니다.
    """
    parts = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("### "):
            parts.append(f'<h3 class="suggestion-heading">{escape(stripped[4:])}</h3>')
        else:
            parts.append(f"<p>{escape(stripped)}</p>")
    return "".join(parts)


def _render_suggestion_code(code: str) -> str:
    """제안 코드 블록을 줄 번호와 함께 보여줍니다.

    번호 없이 붙여넣으면 "몇 번째 줄을 어떻게 고치라는 건지" 를 원본 코드와 눈으로
    맞대 보기 번거로워서, 소스 전체 표(``_render_source_with_highlights``)와 같은
    ``gutter``/``code`` 셀을 재사용해 왼쪽에 줄 번호를 붙입니다. 이 코드는 잘라낸
    예시일 뿐이라 1번째 줄부터 다시 셉니다 — 원본 파일에서의 실제 줄 번호는 바로
    위 설명(``### 줄 N``)에 있습니다.
    """
    rows = "".join(
        f'<tr><td class="gutter">{i}</td><td class="code">{escape(line)}</td></tr>'
        for i, line in enumerate(code.splitlines(), start=1)
    )
    return f'<table class="diff suggestion-code">{rows}</table>'


def _render_suggestion_body(body: str) -> str:
    """```코드 펜스``` 는 강조된 코드 블록으로, 나머지는 문단으로 렌더링합니다."""
    rendered = []
    pos = 0
    for match in _FENCE_RE.finditer(body):
        rendered.append(_render_prose(body[pos:match.start()]))
        code = match.group(1).rstrip("\n")
        rendered.append(_render_suggestion_code(code))
        pos = match.end()
    rendered.append(_render_prose(body[pos:]))
    return "".join(rendered)


_KIND_LABEL = {"build": "컴파일 에러", "test": "테스트 실패", "passed": "통과"}


def _render_attempts_table(attempts: list) -> str:
    """시도별 실패 내역을 표로 보여줍니다.

    화면 로그(``render_result``)에도 같은 내용이 찍히지만, 터미널 텍스트 줄로만
    보면 시도가 여러 번일 때 어디서 뭐가 다른지 한눈에 비교하기 어렵습니다.
    """
    if not attempts:
        return ""
    rows = []
    for attempt in attempts:
        label = _KIND_LABEL.get(attempt.kind, attempt.kind)
        badge = f'<span class="kind-badge {escape(attempt.kind)}">{escape(label)}</span>'
        rows.append(
            f'<tr><td class="index">{attempt.index}</td><td class="kind">{badge}</td>'
            f'<td class="summary">{escape(attempt.summary)}</td></tr>'
        )
    return (
        '<table class="attempts">'
        "<thead><tr><th>시도</th><th>종류</th><th>실패 요약</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table>"
    )


def _render_test_cases_table(cases: list[dict]) -> str:
    """개별 테스트 케이스 전부를 통과/실패와 함께 표로 보여줍니다.

    "테스트 파일 N개" 는 몇 개를 도는지일 뿐이라, 실제로 몇 개의 개별 테스트가
    통과/실패했는지는 이 표로만 보입니다(예: 파일 78개 안에 테스트가 520개).
    """
    if not cases:
        return ""
    rows = []
    # 실패한 케이스를 먼저 보여줍니다 — 556개 중 4개처럼 실패가 드물면
    # 표 맨 아래로 밀려 스크롤해야만 보이는 문제가 있었습니다. 통과/실패
    # 그룹 내부의 원래 순서는 그대로 유지합니다(``sort`` 는 안정 정렬).
    ordered_cases = sorted(cases, key=lambda c: c["passed"])
    for case in ordered_cases:
        cls = "passed" if case["passed"] else "test"
        label = "통과" if case["passed"] else "실패"
        badge = f'<span class="kind-badge {cls}">{label}</span>'
        rows.append(
            f'<tr><td class="kind">{badge}</td>'
            f'<td class="summary">{escape(case["name"])}</td></tr>'
        )
    failed = sum(1 for c in cases if not c["passed"])
    return (
        f"<p class=\"lead\">테스트 {len(cases)}개 중 {failed}개 실패.</p>"
        '<table class="attempts">'
        "<thead><tr><th>결과</th><th>테스트 이름</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table>"
    )


def render_suggestion_html(
    file_name: str,
    source: str,
    body: str,
    attempts: list | None = None,
    test_files: list[tuple[str, str]] | None = None,
    test_cases: list[dict] | None = None,
) -> str:
    """자동 수정 실패 뒤에 남기는 "일반 결함 제안" 을 보여주는 정적 HTML 입니다.

    diff 가 아니라 사람이 읽는 제안 텍스트라서, ``render_diff_html`` 과 레이아웃이
    다릅니다. 제안이 "몇 번째 줄" 이라고만 말하면 그 줄이 실제로 어딘지 찾아보기
    번거로우므로, 소스 전체를 줄 번호와 함께 보여주고 제안이 언급한 줄을 직접
    강조합니다(``_extract_referenced_lines``·``_render_source_with_highlights``).
    그 아래에 제안 본문을 보여주는데, ```코드 펜스```로 감싼 부분만 따로 강조된
    코드 블록으로 렌더링합니다(``_render_suggestion_body``). ``attempts`` 를 주면
    (``state.attempt_log``) 시도별 실패 내역을 표로도 보여줍니다 — 화면 로그와
    같은 내용이지만, 시도가 여러 번이면 표가 비교하기 더 쉽습니다. ``test_files``
    를 주면(``locate_test_files``) "무엇을 통과시켜야 하는가" 인 명세도 그대로
    보여줍니다 — 참고용일 뿐, 이 파일들은 수정 대상이 아닙니다. ``test_cases`` 를
    주면(``extract_test_cases``) 개별 테스트 전부의 통과/실패를 표로 보여줍니다 —
    "테스트 파일 N개" 와는 다른 숫자입니다(파일 하나에 테스트가 여러 개 있을 수
    있음). 개수가 많을 수 있어 접어 둡니다.
    자동으로 적용되는 것이 없다는 점을 맨 위에서 분명히 밝힙니다.
    """
    attempts = attempts or []
    highlighted = _extract_referenced_lines(body)
    source_rows = _render_source_with_highlights(source, highlighted)
    attempts_table = _render_attempts_table(attempts)

    # 가장 궁금한 것(제안 내용)을 맨 위에 두고, 부피가 큰 것(대상 코드 전체·테스트
    # 코드 전체)은 접어 둡니다 — 펼쳐야 보이니 스크롤이 줄고, 필요하면 그대로 볼 수
    # 있습니다.
    summary_line = (
        f"{len(attempts)}회 시도, 전부 실패했습니다."
        if attempts
        else "자동 수정을 시도하지 못했습니다."
    )
    attempts_card = (
        f'<div class="file-card"><div class="file-header">시도별 실패 내역 ({len(attempts)}회)</div>'
        f'<div class="diff-scroll">{attempts_table}</div></div>'
        if attempts_table
        else ""
    )
    test_cards = "".join(
        '<details class="file-card"><summary>'
        f"{escape(name)} <span class=\"test-badge\">테스트 코드 · 참고용</span></summary>"
        '<div class="diff-scroll"><table class="diff">'
        f"{_render_source_with_highlights(content, set())}</table></div></details>"
        for name, content in (test_files or [])
    )
    source_card = (
        '<details class="file-card"><summary>'
        f"{escape(file_name)} (대상 코드 전체 보기)</summary>"
        f'<div class="diff-scroll"><table class="diff">{source_rows}</table></div>'
        "</details>"
    )
    test_cases_table = _render_test_cases_table(test_cases or [])
    test_cases_card = (
        '<details class="file-card"><summary>'
        f"개별 테스트 케이스 전체 ({len(test_cases)}개)</summary>"
        f'<div class="diff-scroll">{test_cases_table}</div></details>'
        if test_cases
        else ""
    )
    return (
        "<!doctype html><html><head><meta charset=\"utf-8\">"
        "<title>결함 제안</title>"
        f"<style>{_STYLE}{_SUGGESTION_STYLE}</style></head><body><div class=\"page\">"
        "<h1>일반 결함 제안 (자동 수정 실패 · 참고용)</h1>"
        '<div class="file-card"><div class="notice-card">'
        f"<p class=\"lead\">{escape(file_name)} — {summary_line} 업무 로직 판단이 "
        "필요한 원인은 다루지 않고, null·경계값처럼 언어 공통적으로 흔한 결함만 "
        "훑어 제안한 것입니다 — 적용 여부는 직접 판단하십시오. 아래 '대상 코드 전체 "
        "보기'를 펼치면 노란 줄로 표시됩니다.</p>"
        f'<div class="notice-body">{_render_suggestion_body(body)}</div>'
        "</div></div>"
        f"{attempts_card}{test_cases_card}{test_cards}{source_card}"
        "</div></body></html>"
    )


def render_pass_report_html(test_cmd: str, test_cases: list[dict]) -> str:
    """애초에 고칠 게 없었을 때(테스트가 전부 통과) 남기는 결과 리포트입니다.

    실패했을 때만 ``notice.html``/``patch.html`` 을 띄우던 것과 달리, 통과했을
    때도 "정말 다 통과했는지" 를 직접 눈으로 확인할 수 있게 개별 테스트 케이스
    표를 그대로 보여줍니다. 실패 사례가 없으니 강조할 줄도, 시도 내역도 없어
    구조가 훨씬 단순합니다.
    """
    test_cases_table = _render_test_cases_table(test_cases or [])
    test_cases_card = (
        f'<div class="file-card"><div class="file-header">'
        f"개별 테스트 케이스 전체 ({len(test_cases)}개)</div>"
        f'<div class="diff-scroll">{test_cases_table}</div></div>'
        if test_cases
        else ""
    )
    return (
        "<!doctype html><html><head><meta charset=\"utf-8\">"
        "<title>결과 리포트</title>"
        f"<style>{_STYLE}{_SUGGESTION_STYLE}</style></head><body><div class=\"page\">"
        "<h1>결과 리포트 (고칠 것 없음 · 전부 통과)</h1>"
        '<div class="file-card"><div class="notice-card">'
        f"<p class=\"lead\">{escape(test_cmd)} 실행 결과, 별도 수정 없이 테스트가 "
        "전부 통과했습니다.</p></div></div>"
        f"{test_cases_card}"
        "</div></body></html>"
    )
