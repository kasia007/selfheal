"""출력 — 화면 로그 · diff · report.json · exit code.

출력은 세 층으로 나눕니다.

1. **실시간 로그** — 사람이 보면서 이해하는 것. 진행 중인 노드와 시도 횟수.
2. **최종 결과** — 끝나고 판단하는 것. 성공이면 diff, 실패면 시도별 실패 사유.
3. **산출물 파일** — 나중에 비교하는 것. ``.heal/report.json``, ``patch.diff``, ``trace.log``.

exit code 를 여러 갈래로 나눈 이유가 있습니다.
**"버그가 없어서 안 고친 것"(2)과 "못 고친 것"(1)은 완전히 다른 결과**인데,
이걸 뭉개면 CI 에 붙일 수 없습니다.

같은 이유로 **"모델을 못 써서 시도조차 못 한 것"(5)** 도 따로 둡니다. 이건 코드 결함이
아니라 인프라 제약이라 재실행하면 고쳐질 수 있고, 그래서 훅에서 푸시를 막아서도 안 됩니다.
"""

from __future__ import annotations

import difflib
import json
from pathlib import Path
from typing import Any

# ── exit code ───────────────────────────────────────────────────
EXIT_FIXED = 0          # 고침 (--apply 로 실제 적용됨)
EXIT_NOT_FIXED = 1      # 못 고침 (재시도 소진)
EXIT_NOTHING_TO_FIX = 2 # 고칠 게 없음 (테스트 전부 통과)
EXIT_PRECONDITION = 3   # 전제 실패 (툴체인 없음 / 테스트 없음 / 언어 미지원)
EXIT_PROPOSED = 4       # 검증된 수정안을 냈고 사용자 승인을 기다림 (기본 동작)
EXIT_LLM_UNAVAILABLE = 5  # 모델을 쓸 수 없었음 — 진단과 참고 사례만 제시

STATUS_TO_EXIT = {
    "fixed": EXIT_FIXED,
    "proposed": EXIT_PROPOSED,
    "failed": EXIT_NOT_FIXED,
    "nothing_to_fix": EXIT_NOTHING_TO_FIX,
    "precondition": EXIT_PRECONDITION,
    "llm_unavailable": EXIT_LLM_UNAVAILABLE,
}


class Console:
    """진행 로그를 찍고, 동시에 trace 버퍼에 쌓아 둡니다.

    **여기가 비밀값 마스킹의 단일 지점입니다(체크리스트 #6).** 화면과 ``trace.log`` 로
    나가는 모든 문자열이 이 클래스를 통과하므로, 마스킹을 여기 한 곳에 걸면 호출부마다
    따로 챙기지 않아도 됩니다. Bedrock 프롬프트 본문은 이 클래스를 거치지 않고 직접
    모델로 갑니다 — 마스킹된 사본을 모델에 주면 파일 재생성 특성상 마스크 문자열이
    코드에 그대로 남을 수 있기 때문입니다 (``src/guardrails.py`` 참고).
    """

    def __init__(self, quiet: bool = False):
        self.quiet = quiet
        self.trace: list[str] = []
        self.secret_hits: list[str] = []
        """마스킹이 실제로 일어난 규칙 이름 목록 (값 자체는 담지 않습니다)."""

    def log(self, message: str = "") -> None:
        message = self._mask(message)
        self.trace.append(message)
        if not self.quiet:
            print(message)

    def step(self, icon: str, message: str) -> None:
        self.log(f"{icon} {message}")

    def detail(self, message: str) -> None:
        self.log(f"   {message}")

    def record(self, message: str) -> None:
        """화면에는 안 띄우고 trace.log 에만 남깁니다. (프롬프트/응답 전문)"""
        self.trace.append(self._mask(message))

    def _mask(self, message: str) -> str:
        from .guardrails import mask_secrets

        masked, hits = mask_secrets(message)
        for name in hits:
            if name not in self.secret_hits:
                self.secret_hits.append(name)
        return masked


def render_memory_hits(console: Console, hits: list) -> None:
    """검색된 과거 사례를 유사도·발생 횟수와 함께 보여줍니다.

    거리(distance)가 아니라 유사도로 환산해서 찍습니다. "거리 0.18" 은 직관에 반합니다.
    """
    if not hits:
        console.step("🔎", "유사 버그 검색 ... 없음 (첫 발생)")
        return

    console.step("🔎", f"유사 버그 검색 ... {len(hits)}건")
    for i, hit in enumerate(hits, start=1):
        warn = " ⚠️" if hit.occurrences >= 3 else "  "
        console.log(
            f"   [{i}] 유사도 {hit.similarity:.2f}{warn} {hit.occurrences}회 발생"
        )
        for line in hit.document.splitlines():
            if line.strip():
                console.log(f"       {line.strip()}")
        meta = []
        if hit.languages:
            meta.append(f"언어: {hit.languages}")
        if hit.first_seen:
            meta.append(f"최초 {hit.first_seen}")
        if hit.last_seen:
            meta.append(f"최근 {hit.last_seen}")
        if meta:
            console.log(f"       {'   |   '.join(meta)}")

        # 어느 검색기가 이 문서를 찾았는지 — 하이브리드의 기여를 눈으로 확인하려면
        # 최종 순위만으로는 알 수 없습니다. 벡터가 놓치고 BM25 가 건진 것이 보여야 합니다.
        parts = [
            f"벡터 {hit.vector_rank}위" if hit.vector_rank else "벡터 −",
            f"BM25 {hit.bm25_rank}위" if hit.bm25_rank else "BM25 −",
        ]
        console.log(
            f"       검색: {' · '.join(parts)} → 최종 {hit.final_rank}위 (보정 ×{hit.boost:.2f})"
        )


def make_diff(before: str, after: str, filename: str) -> str:
    """수정 결과는 전체 소스가 아니라 diff 로 보여줍니다.

    LLM 이 만든 파일 전문을 그대로 뿌리면 무엇이 바뀌었는지 알 수 없습니다.
    diff 여야 리뷰가 가능한 형태가 됩니다.
    """
    return "".join(
        difflib.unified_diff(
            before.splitlines(keepends=True),
            after.splitlines(keepends=True),
            fromfile=f"a/{filename}",
            tofile=f"b/{filename}",
        )
    )


def render_result(console: Console, state, diff: str) -> None:
    """최종 결과를 찍습니다. 성공이면 diff, 실패면 시도별 사유입니다."""
    console.log("")
    if state.status == "proposed":
        # 기본 동작입니다. 검증까지 마친 수정안을 보여 주고 원본은 건드리지 않습니다.
        console.step("📋", f"수정안 준비 완료 (샌드박스에서 테스트 통과, 시도 {state.attempts}회)")
        console.detail("원본 파일은 수정하지 않았습니다. 아래 diff 를 검수하십시오.")
        if diff:
            console.log("")
            console.log(diff.rstrip())
        console.log("")
        console.detail("적용하려면 같은 명령에 --apply 를 붙여 다시 실행하십시오.")
    elif state.status == "fixed":
        console.step("✅", f"적용 완료 (시도 {state.attempts}회)")
        if diff:
            console.log("")
            console.log(diff.rstrip())
    elif state.status == "nothing_to_fix":
        console.step("✅", "테스트가 모두 통과합니다. 고칠 것이 없습니다.")
    elif state.status == "llm_unavailable":
        # 모델을 못 썼습니다. 고치지는 못했지만 규칙 기반으로 모은 단서는 남깁니다 —
        # 크래시로 아무것도 안 남기는 것보다 훨씬 낫습니다.
        console.step("⚠️", "모델을 쓸 수 없어 수정하지 못했습니다.")
        console.detail(f"사유: {state.llm_error}")
        if state.target_file:
            console.detail(f"진단된 수정 대상: {state.target_file}")
        if state.attempt_log:
            last = state.attempt_log[-1]
            console.detail(f"테스트 실패: {last.summary}")
        if state.memory_hits:
            console.log("")
            console.detail(f"과거 참고 사례 {len(state.memory_hits)}건:")
            for hit in state.memory_hits:
                console.detail(f"  · ({hit.occurrences}회 발생) {hit.title}")
            if getattr(state, "memory_degraded_query", False):
                console.detail(
                    "  ※ 질의어를 LLM 없이 원시 실패 출력으로 만들었습니다. "
                    "정확도가 평소보다 낮으니 참고용으로만 보십시오."
                )
        else:
            console.detail("참고할 과거 사례를 찾지 못했습니다.")
        console.log("")
        console.detail("→ 원본 파일은 수정되지 않았습니다. 한도가 회복된 뒤 다시 실행하십시오.")
    else:
        console.step("❌", f"{state.attempts}회 시도 후 실패")
        for attempt in state.attempt_log:
            label = {"build": "컴파일 에러", "test": "테스트 실패"}.get(
                attempt.kind, attempt.kind
            )
            console.detail(f"시도 {attempt.index}: {label} ({attempt.summary})")
        # 모든 시도는 샌드박스 사본에서 이뤄지므로 원본은 처음부터 그대로입니다.
        console.detail("→ 원본 파일은 수정되지 않았습니다.")


def write_artifacts(
    workdir: Path, state, diff: str, console: Console, extra: dict[str, Any] | None = None
) -> Path:
    """``.heal/`` 에 report.json · patch.diff · trace.log 를 남깁니다."""
    out = workdir / ".heal"
    out.mkdir(parents=True, exist_ok=True)

    (out / "report.json").write_text(
        json.dumps(state.to_report(extra), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    if diff:
        (out / "patch.diff").write_text(diff, encoding="utf-8")
    (out / "trace.log").write_text("\n".join(console.trace), encoding="utf-8")
    return out


def render_stats(console: Console, rows: list[dict[str, Any]]) -> None:
    """누적 버그 패턴 리포트입니다.

    이게 이 프로젝트가 self-healing 을 넘어서 주는 가치입니다.
    자동으로 고치는 것보다, **이 코드베이스가 반복해서 저지르는 실수**를 알려주는 쪽이
    실제로는 더 쓸모가 있습니다.
    """
    if not rows:
        console.log("누적된 버그 패턴이 없습니다.")
        return
    total = sum(r["occurrences"] for r in rows)
    console.log(f"누적 버그 패턴 (총 {len(rows)}종 / {total}건)")
    console.log("")
    for row in rows:
        console.log(f"  {row['occurrences']:>2}회  {row['pattern'][:40]:<42}{row['languages']}")
