"""agent.py — 메인 에이전트 그래프 + CLI 진입점.

사용자가 전달하는 것은 **디렉터리 하나**뿐입니다.

    python -m src.agent ./data/samples/py-index

언어, 테스트 명령, 고칠 파일은 전부 여기서 추론합니다.
추론 결과는 화면에 그대로 찍어서, 에이전트가 무엇을 근거로 판단했는지 보이게 합니다.

**기본 동작은 "제안" 입니다. 사용자 승인 없이 원본 파일을 고치지 않습니다.**

    수정과 검증은 작업 디렉터리의 **임시 사본(샌드박스)** 에서 이뤄집니다.
    그래서 사용자에게 diff 를 낼 때는 이미 "이 패치는 테스트를 통과한다" 가
    확인된 상태입니다. 원본에 쓰는 것은 ``--apply`` 를 붙였을 때뿐입니다.

    python -m src.agent ./data/samples/py-index            # 제안만 (원본 불변)
    python -m src.agent ./data/samples/py-index --apply    # 검수 후 실제 적용

exit code
    0  고침 (--apply 로 실제 적용)
    1  못 고침 (재시도 소진)
    2  고칠 게 없음 (테스트 전부 통과)
    3  전제 실패 (툴체인 없음 / 테스트 없음 / 언어 미지원)
    4  검증된 수정안 준비됨, 사용자 승인 대기 (기본 동작)
    5  모델을 쓸 수 없었음 — 진단과 참고 사례만 제시 (한도·인증·네트워크)

이 파일은 세 조각으로 구성됩니다.

1. **그래프 배선** (``build_graph``) — 원본 노트북의 골격을 그대로 유지합니다.
   바뀐 것은 노드 **안**의 구현이지 그래프 모양이 아닙니다. 언어를 추가해도
   이 부분은 손댈 일이 없습니다.

       test_execution
           ├ 통과            → END
           ├ 재시도 소진      → END
           └ 실패            → locate → bug_report → memory_search
                                            ├ 결과 없음 → memory_generation ┐
                                            └ 결과 있음 → memory_filter      │
                                                  ├ 병합 대상 없음 → ────────┤
                                                  └ 병합 대상 있음 → memory_modification
                                                            (남으면 자기 반복) │
                                                                              ↓
                                                                         code_update
                                                                              ↓
                                                                        code_patching
                                                                              ↓
                                                                      test_execution (루프백)

2. **``HealingNodes``** — 실제로 고치는 일을 하는 노드 9개입니다. 원본 노트북 대비
   가장 중요한 변경은 ``code_update_node`` 에 있습니다.

       원본은 메모리를 검색해 놓고 정작 **수정 프롬프트에 넣지 않습니다.**
       그래서 벡터DB 파이프라인 전체가 수정 품질에 아무 기여를 하지 않습니다.
       메모리를 켜든 끄든 결과가 같아지는, 측정 불가능한 구조입니다.

   여기서는 검색·필터링한 과거 사례를 수정 프롬프트에 실제로 주입합니다.
   그래야 "메모리가 도움이 됐다" 를 주장이 아니라 **재시도 횟수**로 증명할 수 있습니다.
   판단 근거는 ``report.json`` 의 ``memory_hits[].injected`` 와 ``attempts`` 입니다.

3. **CLI 진입점** (``main``) — 언어 감지 · preflight · 스캔 집계 · 샌드박스 준비 ·
   그래프 실행 · 산출물 기록까지 담당합니다. ``run.sh`` 가 이 모듈을
   ``python -m src.agent`` 로 호출합니다.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import webbrowser
from pathlib import Path

from langchain_core.exceptions import OutputParserException
from langchain_core.messages import HumanMessage
from langchain_core.output_parsers import PydanticOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langgraph.graph import END, StateGraph
from pydantic import BaseModel

from .report import (
    EXIT_PRECONDITION,
    EXIT_PROPOSED,
    STATUS_TO_EXIT,
    Console,
    make_diff,
    render_memory_hits,
    render_result,
    render_stats,
    write_artifacts,
)
from .diffview import render_diff_html, render_pass_report_html, render_suggestion_html
from .guardrails import detect_injection, wrap_untrusted
from .retriever import DEFAULT_MERGE_THRESHOLD, MemoryStore
from .review import build_suggestion_prompt
from .schemas import BugPattern, BugReport, TargetChoice
from .state import Attempt, State
from .tracing import configure_tracing
from .tools import (
    ADAPTERS,
    EXCLUDED_DIRS,
    LanguageAdapter,
    LanguageDetectionError,
    detect_language,
    extract_symbols,
    extract_test_cases,
    find_git_root,
    count_dirty_files,
    find_project_root,
    is_git_dirty,
    iter_source_files,
    link_or_copy_dir,
    locate_targets,
    locate_test_files,
    preflight,
    resolve_test_cmd,
    scan_workdir,
)

# AGENTS.md 고정 스택 — **기본값은 바꾸지 않습니다.**
LLM_MODEL_ID = "us.anthropic.claude-sonnet-4-5-20250929-v1:0"
BEDROCK_REGION = "us-east-1"

# 자주 쓰는 모델의 별칭입니다. Bedrock 모델 ID 전체를 외우지 않아도 되게 합니다.
MODEL_ALIASES = {
    "sonnet": "us.anthropic.claude-sonnet-4-5-20250929-v1:0",
    "haiku": "us.anthropic.claude-haiku-4-5-20251001-v1:0",
    "haiku-3-5": "us.anthropic.claude-3-5-haiku-20241022-v1:0",
}


def resolve_model_id(override: str | None = None) -> str:
    """쓸 모델 ID 를 정합니다. 우선순위는 CLI > 환경변수 > AGENTS.md 기본값입니다.

    기본값은 AGENTS.md 가 정한 Sonnet 4.5 그대로입니다. override 를 둔 이유는 하나뿐인데,
    **일일 토큰 한도(ThrottlingException: Too many tokens per day)에 걸리면 그날은 어떤
    검증도 못 하게 되기 때문**입니다. 그때 더 가벼운 모델로 갈아타 파이프라인 검증을
    이어갈 수 있어야 합니다. 별칭(``sonnet``·``haiku``)이나 전체 모델 ID 를 받습니다.
    """
    value = (override or os.environ.get("BEDROCK_MODEL_ID") or "").strip()
    if not value:
        return LLM_MODEL_ID
    return MODEL_ALIASES.get(value.lower(), value)

TEST_TIMEOUT_SEC = 180

# 메모리 저장/검색에 공통으로 쓰는 형식입니다.
# **저장할 때와 찾을 때의 표현을 맞춰야** 임베딩이 서로 맞물립니다.
# (원본 노트북의 좋은 아이디어라 그대로 가져왔습니다.)
MEMORY_FORMAT = "# 패턴명 ## 증상 ### 원인과 해결"


class LLMUnavailable(RuntimeError):
    """모델을 지금 쓸 수 없습니다 — 한도 초과·인증 실패·네트워크 오류.

    **파싱 실패와는 다릅니다.** 파싱 실패는 모델이 답을 줬는데 형식이 틀린 것이라 텍스트
    경로로 폴백하면 되지만(15단계), 이건 답 자체를 받을 수 없는 상황입니다. 그래서 수정을
    포기하고 축약 경로(규칙 기반 진단 + 메모리 참고 사례)로 끝냅니다.

    원인을 세분하지 않는 이유는 사용자 입장에서 결과가 같기 때문입니다 — "지금은 못 고친다".
    다만 예외 종류와 메시지는 리포트에 남겨 무엇이 문제였는지 구분할 수 있게 합니다.
    """


def _tail(text: str, lines: int = 40) -> str:
    """LLM 에 넘길 때 출력이 너무 길면 뒤쪽만 씁니다. 실패 원인은 보통 끝에 있습니다."""
    rows = text.strip().splitlines()
    return "\n".join(rows[-lines:])


def _one_line(text: str, adapter: LanguageAdapter | None = None) -> str:
    """시도 로그에 남길 짧은 요약을 뽑습니다.

    ``adapter.error_summary_line_pattern`` 이 있으면 그 패턴에 맞는 줄을 뒤에서부터
    우선 찾습니다. 파이썬은 트레이스백 마지막 줄이 곧 에러 메시지라 문제가 없지만,
    Node.js 는 에러 객체를 여러 줄짜리 속성 목록으로 찍고 그게 ``}`` 한 글자짜리
    줄로 끝나는 경우가 흔해서, 아무 줄이나 집으면 그 의미 없는 ``}`` 가 뽑힙니다.
    패턴이 없거나 못 찾으면 예전처럼 맨 끝의 비어있지 않은 줄로 대체합니다.

    "Expected values to be strictly equal:" 처럼 **핵심 값이 다음 줄에 이어지는**
    메시지도 있어서, 찾은 줄 하나만으로는 뭐가 문제인지 안 보일 수 있습니다. 그래서
    그 줄 뒤에 바로 이어지는 짧은 값 줄(예: ``404 !== 200``)까지 몇 줄 더 붙입니다 —
    스택트레이스(``at ...``)나 다음 구획(``=`` 배너)이 나오면 거기서 멈춥니다.
    """
    lines = [line.strip() for line in text.strip().splitlines() if line.strip()]
    if not lines:
        return "(출력 없음)"

    def _window(start: int) -> str:
        collected = [lines[start]]
        for extra in lines[start + 1 : start + 3]:
            if extra.startswith("at ") or extra.startswith("="):
                break
            collected.append(extra)
        return " ".join(collected)[:200]

    pattern = adapter.error_summary_line_pattern if adapter else ""
    if pattern:
        for idx in range(len(lines) - 1, -1, -1):
            if not lines[idx].startswith("=") and re.search(pattern, lines[idx]):
                return _window(idx)

    for idx in range(len(lines) - 1, -1, -1):
        if not lines[idx].startswith("="):
            return _window(idx)
    return "(출력 없음)"


def commit_pending_memory_writes(memory: MemoryStore, state: State, console: Console) -> None:
    """실제로 테스트를 통과시킨 시도의 진단만 메모리에 남깁니다.

    ``memory_modification_node``/``memory_generation_node`` 는 시도마다
    ``state.pending_memory_writes`` 에 예약만 남기고, 실제 ``memory.add``/
    ``memory.merge`` 호출은 이 함수(그래프가 다 끝나고 ``state.status`` 가 확정된
    뒤)에서만 이뤄집니다. 라우터 미등록처럼 한 번도 안 고쳐지는 진단이 시도할
    때마다 메모리에 쌓이는 문제(발생 횟수만 계속 올라가는 노이즈)를 구조적으로
    막기 위해서입니다. ``state.status != "fixed"`` 면 예약은 그냥 버려집니다.

    ``main()`` 뿐 아니라 그래프를 직접 돌리는 코드(배선 테스트 등)에서도, 메모리에
    실제로 남기고 싶으면 이 함수를 그래프 실행 뒤에 호출해야 합니다.
    """
    if state.status != "fixed":
        return
    for write in state.pending_memory_writes:
        if write["kind"] == "add":
            memory.add(
                write["summary"], write["language"], write["target"],
                pattern=write["pattern"],
            )
            console.step("💾", "새 패턴으로 저장 (1회 발생, 실제로 고쳐진 것 확인됨)")
        else:
            count = memory.merge(
                write["memory_id"], write["summary"], write["language"],
                write["target"], pattern=write["pattern"],
            )
            console.step(
                "🧠", f"기존 패턴에 병합 → 누적 {count}회 발생 (실제로 고쳐진 것 확인됨)"
            )


def _exception_names(output: str) -> list[str]:
    """테스트 출력에서 예외·에러 이름을 뽑습니다.

    ``IndexError`` · ``KeyError`` · ``NullPointerException`` 처럼 ``Error``/``Exception``
    으로 끝나는 CamelCase 식별자를 찾습니다. 언어에 무관한 관례라 어댑터마다 정규식을
    두지 않아도 됩니다. 축약 경로(LLM 없음)의 쿼리 확장 재료입니다.
    """
    names = re.findall(r"([A-Z][A-Za-z0-9_]*(?:Error|Exception))", output)
    seen: list[str] = []
    for name in names:
        if name not in seen:
            seen.append(name)
    return seen[:5]


def detect_newline(path: Path) -> str:
    """파일이 쓰는 줄바꿈 방식을 알아냅니다. 못 읽으면 ``\\n`` 입니다.

    ``newline=""`` 로 열어야 파이썬이 CRLF 를 LF 로 바꾸지 않고 원문을 그대로 줍니다.
    첫 줄바꿈만 보면 충분합니다 — 한 파일에 두 방식이 섞인 경우는 어차피 다수를 따를 수밖에
    없고, 그럴 때 첫 줄의 방식이 가장 그럴듯한 선택입니다.
    """
    try:
        with path.open("r", encoding="utf-8", newline="") as fp:
            chunk = fp.read(8192)
    except OSError:
        return "\n"
    return "\r\n" if "\r\n" in chunk else "\n"


def write_source(path: Path, text: str, newline: str) -> None:
    """원본의 줄바꿈 방식을 유지하며 소스를 씁니다.

    ``write_text`` 를 그냥 쓰면 CRLF 파일이 LF 로 정규화되어, 고치지 않은 줄까지 전부
    변경으로 diff 에 나옵니다. 그러면 "diff 를 검수한다" 는 이 프로젝트의 승인 흐름(G6)이
    무력화됩니다.
    """
    with path.open("w", encoding="utf-8", newline=newline) as fp:
        fp.write(text)


def _strip_fence(text: str) -> str:
    """LLM 이 코드펜스를 붙여 보내면 벗겨 냅니다.

    프롬프트로 금지해도 가끔 붙여 보내므로 방어합니다.
    이걸 안 벗기면 파일 첫 줄이 ```python 이 되어 무조건 컴파일 에러가 납니다.
    """
    text = text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines)
    return text.strip() + "\n"


class HealingNodes:
    """노드 묶음입니다.

    AGENTS.md 규약에 따라 **모듈 최상단에서 실제 모델을 만들지 않습니다.**
    ``llm=None`` 을 받아 주입 가능하게 두어, 가짜 모델로 배선만 검증할 수 있게 합니다.
    """

    def __init__(
        self,
        adapter: LanguageAdapter,
        memory: MemoryStore,
        console: Console,
        llm=None,
        model_id: str | None = None,
        real_workdir: Path | None = None,
        git_root: Path | None = None,
    ):
        self.adapter = adapter
        self.memory = memory
        self.console = console
        self._llm = llm
        self.model_id = model_id or resolve_model_id()
        # 수정 대상을 "커밋 안 된 파일" 로 제한하는 가드레일(G12)에만 씁니다.
        # 샌드박스 사본(``state.workdir``)에는 ``.git`` 이 없을 수 있어, git 상태는
        # 항상 사용자의 실제 작업 디렉터리 기준으로 판단해야 합니다. 둘 다 없으면
        # (모노레포가 아니거나 git 리포지토리가 아니면) 가드레일은 조용히 꺼집니다.
        self.real_workdir = real_workdir
        self.git_root = git_root

    @property
    def llm(self):
        if self._llm is None:
            from langchain_aws import ChatBedrock

            self._llm = ChatBedrock(
                model_id=self.model_id,
                region_name=BEDROCK_REGION,
                model_kwargs={"temperature": 0},
            )
        return self._llm

    def _ask(self, prompt: str, *, name: str = "ask", **meta) -> str:
        """LLM 한 번 호출. 프롬프트와 응답 전문은 trace.log 에 남깁니다.

        ``name`` 과 ``meta`` 는 LangSmith 트레이스에 붙는 런 이름·메타데이터입니다.
        추적이 꺼져 있으면 무해하게 무시됩니다(체크리스트 #11).
        """
        self.console.record(f"\n--- PROMPT ---\n{prompt}")
        try:
            response = self.llm.invoke(
                [HumanMessage(content=prompt)],
                config=self._run_config(name, meta),
            ).content
        except Exception as exc:
            # 여기서 그냥 올려 보내면 raw traceback 으로 프로세스가 죽고 산출물도 남지
            # 않습니다. 타입 있는 예외로 감싸 노드가 축약 경로로 전환할 수 있게 합니다.
            raise LLMUnavailable(f"{type(exc).__name__}: {exc}") from exc
        if isinstance(response, list):  # Bedrock 이 블록 리스트를 줄 때가 있습니다.
            response = "".join(
                part.get("text", "") if isinstance(part, dict) else str(part)
                for part in response
            )
        response = response.strip()
        self.console.record(f"\n--- RESPONSE ---\n{response}")
        return response

    @staticmethod
    def _run_config(name: str, meta: dict) -> dict:
        """LangSmith 런 이름·태그·메타데이터입니다.

        추적이 꺼져 있어도 그냥 무시되는 값이라, 켜짐/꺼짐을 코드에서 분기하지 않습니다.
        """
        return {
            "run_name": f"selfheal.{name}",
            "tags": ["selfheal", name],
            "metadata": {k: v for k, v in meta.items() if v is not None},
        }

    def _ask_structured(
        self, prompt: str, model: type[BaseModel], *, name: str = "", **meta
    ) -> BaseModel | None:
        """LCEL 체인으로 **구조화된** 답을 받습니다. 실패하면 ``None`` 입니다.

            ChatPromptTemplate | llm | PydanticOutputParser

        형식 지시문(``format_instructions``)은 스키마의 ``Field(description=...)`` 에서
        자동 생성됩니다. 그래서 지시문을 프롬프트와 스키마 두 곳에 중복해 적지 않습니다.

        **파싱 실패 시 한 번만 재시도하고, 그래도 실패하면 ``None`` 을 돌려줍니다.**
        호출자는 그때 기존 텍스트 경로로 폴백합니다 — 구조화는 품질 장치이지 관문이
        아닙니다. 이 원칙은 G9 검증과 메모리 검색에도 같이 적용돼 있습니다.

        **모델을 아예 쓸 수 없는 경우는 다릅니다** — ``LLMUnavailable`` 로 올려 보냅니다.
        그때 텍스트 경로로 폴백하면 어차피 실패할 호출을 한 번 더 하는 낭비입니다.
        """
        parser = PydanticOutputParser(pydantic_object=model)
        # 프롬프트 본문에 {} 가 있어도 템플릿 변수로 오해되지 않도록 값으로 넘깁니다.
        chain = (
            ChatPromptTemplate.from_messages([("human", "{body}\n\n{format_instructions}")])
            | self.llm
            | parser
        )
        payload = {
            "body": prompt,
            "format_instructions": parser.get_format_instructions(),
        }

        run_name = name or model.__name__
        for attempt in (1, 2):
            self.console.record(f"\n--- PROMPT ({model.__name__}, 시도 {attempt}) ---\n{prompt}")
            config = self._run_config(run_name, {**meta, "parse_attempt": attempt})
            try:
                result = chain.invoke(payload, config=config)
            except OutputParserException as exc:
                # 모델이 답은 줬는데 형식이 틀린 경우입니다. 재시도 → 텍스트 폴백.
                self.console.record(f"\n--- PARSE FAILED ---\n{type(exc).__name__}: {exc}")
                continue
            except Exception as exc:
                # 모델 자체를 쓸 수 없는 경우입니다(한도·인증·네트워크).
                # 텍스트 폴백은 어차피 실패할 호출을 한 번 더 하는 낭비이므로 올려 보냅니다.
                self.console.record(f"\n--- LLM UNAVAILABLE ---\n{type(exc).__name__}: {exc}")
                raise LLMUnavailable(f"{type(exc).__name__}: {exc}") from exc
            self.console.record(f"\n--- RESPONSE ({model.__name__}) ---\n{result!r}")
            return result

        self.console.detail(f"구조화 출력 실패 — 텍스트 경로로 진행합니다 ({model.__name__})")
        return None

    # ── 1. 테스트 실행 ─────────────────────────────────────────
    def test_execution_node(self, state: State) -> State:
        """테스트를 돌려서 '지금 깨져 있는가' 를 판정합니다.

        원본의 ``code_execution_node`` 자리이지만, 함수를 직접 호출하는 대신
        서브프로세스로 테스트를 돌립니다. 부수 효과로 샌드박싱도 해결됩니다.
        LLM 이 만든 코드가 우리 프로세스 안에서 ``exec`` 되지 않기 때문입니다.
        """
        workdir = Path(state.workdir)
        self.console.step("🧪", f"테스트 실행 ... ({' '.join(state.test_cmd)})")

        try:
            proc = subprocess.run(
                state.test_cmd,
                cwd=workdir,
                capture_output=True,
                text=True,
                timeout=TEST_TIMEOUT_SEC,
                shell=False,
            )
            output = (proc.stdout or "") + (proc.stderr or "")
            passed = proc.returncode == 0
        except subprocess.TimeoutExpired:
            output = f"테스트가 {TEST_TIMEOUT_SEC}초 안에 끝나지 않았습니다."
            passed = False
        except OSError as exc:
            output = f"테스트 실행 실패: {exc}"
            passed = False

        state.test_output = output
        state.error = not passed
        state.is_build_failure = (not passed) and self.adapter.is_build_failure(output)

        # "테스트 파일 N개" 는 몇 개를 돌리는지일 뿐, 실제로 몇 개의 개별 테스트가
        # 통과/실패했는지는 다릅니다(예: 파일 78개 안에 테스트가 520개 있을 수 있음).
        # 지원 언어(지금은 javascript)면 실제 개수를 그대로 보여줍니다.
        cases = extract_test_cases(self.adapter, output)
        if cases is not None:
            failed_count = sum(1 for c in cases if not c["passed"])
            self.console.detail(f"테스트 {len(cases)}개 중 {failed_count}개 실패")

        if passed:
            self.console.detail("PASS")
            # 한 번도 고친 적 없이 통과 = 애초에 고칠 게 없었던 것.
            # "못 고침(1)" 과 반드시 구분해야 합니다.
            state.status = "fixed" if state.attempts > 0 else "nothing_to_fix"
            if state.attempts > 0:
                state.attempt_log.append(
                    Attempt(index=state.attempts, kind="passed", summary="통과")
                )
        else:
            kind = "build" if state.is_build_failure else "test"
            summary = _one_line(output, self.adapter)
            self.console.detail(
                ("컴파일 에러" if state.is_build_failure else "FAIL") + f" — {summary}"
            )
            if state.attempts > 0:
                state.attempt_log.append(
                    Attempt(index=state.attempts, kind=kind, summary=summary)
                )
        return state

    # ── 2. 수정 대상 추론 ──────────────────────────────────────
    def locate_node(self, state: State) -> State:
        """사용자가 파일을 주지 않으므로, 실패 출력에서 스스로 찾습니다.

        규칙 기반(정규식)을 먼저 씁니다. 빠르고 공짜이기 때문입니다.
        규칙이 실패할 때만 LLM 에게 물어봅니다.
        """
        # 가드레일(체크리스트 #6) — 축약 경로(모델 불가)는 bug_report_node 를 건너뛰므로
        # test_output 검사가 여기서 이뤄지지 않으면 누락됩니다. 여기서 한 번 해 둡니다.
        self._flag_injection(state, state.test_output)
        workdir = Path(state.workdir)

        # 재시도 중이거나, 1단계에서 사용자가 파일을 지정해 대상이 이미 정해진 경우입니다.
        if state.target_file:
            path = Path(state.target_file)
            if state.attempts == 0:
                # attempts == 0 인데 대상이 이미 있다 = 재시도가 아니라 사용자 지정입니다.
                # (규칙 기반 탐색으로 대상을 정하는 아래 경로는 이 시점에 도달하지 않습니다.)
                # 1단계 결정: 사용자가 지정한 파일이 우선이고, 스택트레이스와 다르면 안내만 합니다.
                targets = locate_targets(self.adapter, state.test_output, workdir)
                if targets and targets[0][0] != path:
                    other_path, other_line = targets[0]
                    other_rel = other_path.relative_to(workdir.resolve())
                    self.console.detail(
                        f"스택트레이스: {other_rel}:{other_line} (지정 파일과 다름)"
                    )
                if self._blocked_by_git_guardrail(state, path, workdir):
                    state.status = "failed"
                    return state
            state.current_source = path.read_text(encoding="utf-8")
            return state

        targets = locate_targets(self.adapter, state.test_output, workdir)
        if targets:
            path, line_no = targets[0]
        elif state.llm_error:
            # 축약 경로 — 모델을 못 쓰는 상태이므로 LLM 폴백을 시도하지 않습니다.
            path = None
            line_no = None
        else:
            try:
                path = self._locate_with_llm(state, workdir)
            except LLMUnavailable as exc:
                state.llm_error = str(exc)
                path = None
            line_no = None

        if path is None:
            self.console.step("⚠️", "수정 대상 파일을 특정하지 못했습니다.")
            # 모델을 못 써서 못 찾은 것과, 규칙·LLM 모두 실패해서 못 찾은 것은 다른 결과입니다.
            state.status = "llm_unavailable" if state.llm_error else "failed"
            return state

        if self._blocked_by_git_guardrail(state, path, workdir):
            state.status = "failed"
            return state

        state.target_file = str(path)
        state.target_line = line_no
        state.original_source = path.read_text(encoding="utf-8")
        state.current_source = state.original_source
        # 아직 아무것도 쓰지 않은 지금이 원본의 줄바꿈을 볼 수 있는 유일한 시점입니다.
        state.newline = detect_newline(path)

        rel = path.relative_to(workdir.resolve()) if path.is_absolute() else path
        loc = f"{rel}:{line_no}" if line_no else str(rel)
        if len(targets) > 1:
            others = ", ".join(
                f"{p.relative_to(workdir.resolve())}:{n}" for p, n in targets[1:]
            )
            self.console.step(
                "📍", f"수정 대상 {len(targets)}개: {loc}, {others} (이번 실행은 1번만 처리)"
            )
        else:
            self.console.step("📍", f"수정 대상 추론: {loc}")
        return state

    def _blocked_by_git_guardrail(self, state: State, path: Path, sandbox: Path) -> bool:
        """G12 — 수정 대상을 커밋 안 된(dirty) 파일로만 제한합니다.

        시나리오: 개발자가 로컬에서 코드를 고치고 커밋 전에 selfheal 을 돌립니다.
        이때 실패한 테스트의 진짜 원인이 자신이 방금 건드린 코드가 아니라 이미
        안정적으로 커밋된 다른 파일에 있을 수 있는데, 그런 파일까지 자동으로
        고치면 지금 작업과 무관한 범위까지 손대게 됩니다. 그래서 대상이 이미
        커밋된(clean) 파일이면 고치지 않고 거부합니다 — **테스트 실행 자체는
        이 가드레일과 무관하게 항상 전체를 그대로 돌립니다.**

        git 리포지토리가 아니거나(``self.git_root is None``) 판단 근거가 없으면
        (``is_git_dirty`` 가 ``None``) 조용히 통과시킵니다 — 판단할 수 없다는
        이유로 핵심 기능 전체를 막지 않습니다.
        """
        if self.git_root is None or self.real_workdir is None:
            return False
        real_path = _user_path(path, sandbox, self.real_workdir)
        if is_git_dirty(real_path, self.git_root) is False:
            rel = (
                real_path.relative_to(self.git_root)
                if real_path.is_absolute()
                else real_path
            )
            self.console.step(
                "🛡️",
                f"수정 대상 거부 — 이미 커밋된 파일입니다: {rel} "
                "(G12 — 커밋 안 된 파일만 고칩니다)",
            )
            self._record_guardrail(state, "G12", [str(rel)])
            return True
        return False

    def _locate_with_llm(self, state: State, workdir: Path) -> Path | None:
        """규칙 기반 파싱이 실패했을 때의 폴백입니다."""
        candidates = [
            p for p in iter_source_files(workdir) if self.adapter.is_source_file(p)
        ]
        if not candidates:
            return None
        if len(candidates) == 1:
            return candidates[0]

        listing = "\n".join(str(p.relative_to(workdir)) for p in candidates)
        question = (
            "테스트 실패 출력을 보고 어느 소스 파일을 고쳐야 하는지 고르십시오.\n"
            + wrap_untrusted(_tail(state.test_output), "테스트 출력") + "\n\n"
            f"[후보 파일]\n{listing}"
        )

        choice = self._ask_structured(
            question, TargetChoice, name="locate", candidates=len(candidates)
        )
        if isinstance(choice, TargetChoice):
            self.console.record(f"\n--- 선택 근거 ---\n{choice.reason}")
            picked = self._match_candidate(choice.path, candidates, workdir)
            if picked is not None:
                return picked

        # 구조화가 실패했거나 고른 경로가 후보에 없을 때만 텍스트 경로로 폴백합니다.
        answer = self._ask(
            question + "\n\n후보 목록에 있는 경로 하나만, 다른 말 없이 출력하십시오.",
            name="locate.fallback",
            candidates=len(candidates),
        )
        return self._match_candidate(answer, candidates, workdir)

    @staticmethod
    def _match_candidate(
        answer: str, candidates: list[Path], workdir: Path
    ) -> Path | None:
        """모델이 말한 경로를 후보 목록의 실제 경로에 맞춰 봅니다.

        구조화 출력을 쓰면 대개 경로가 정확히 오지만, 앞에 ``./`` 를 붙이거나 구분자를
        바꿔 쓰는 정도의 차이는 남습니다. **후보 목록에 없는 경로는 절대 받아들이지
        않습니다** — 모델이 존재하지 않는 파일을 지어내도 여기서 걸립니다.
        """
        cleaned = (answer or "").strip().strip("`\"'").replace("\\", "/").lstrip("./")
        for cand in candidates:
            rel = str(cand.relative_to(workdir)).replace("\\", "/")
            if cleaned == rel or cleaned == cand.name:
                return cand
        # 정확히 일치하지 않으면 포함 관계로 한 번 더 봅니다 (텍스트 폴백 경로용).
        for cand in candidates:
            rel = str(cand.relative_to(workdir)).replace("\\", "/")
            if rel in cleaned or cand.name in cleaned:
                return cand
        return None

    # ── 3. 버그 리포트 ─────────────────────────────────────────
    def bug_report_node(self, state: State) -> State:
        """무엇이 왜 터졌는지 정리합니다. 메모리 검색의 질의어이자 저장 재료입니다."""
        # 가드레일(체크리스트 #6) — 소스·테스트 출력은 저장소에서 왔으므로 신뢰할 수
        # 없습니다. 탐지는 기록만 하고 실행을 막지 않습니다 — 진짜 방어는 G1·G6·G9 처럼
        # 이미 있는 구조적 가드레일입니다. 여기서는 데이터/지시 분리를 한 겹 더합니다.
        self._flag_injection(state, state.current_source, state.test_output)
        question = (
            "다음은 테스트가 실패한 코드입니다. 버그 리포트를 작성하십시오.\n"
            f"[언어] {state.language}\n"
            f"[파일] {Path(state.target_file).name}\n"
            + wrap_untrusted(state.current_source, "소스") + "\n\n"
            + wrap_untrusted(_tail(state.test_output), "테스트 실패 출력")
        )

        try:
            report = self._ask_structured(
                question, BugReport, name="bug_report", language=state.language
            )
        except LLMUnavailable as exc:
            # 축약 경로로 전환합니다. 리포트를 못 만들어도 메모리 검색은 살아 있을 수
            # 있습니다 — 검색은 Sonnet 이 아니라 Titan 임베딩을 쓰기 때문입니다.
            state.llm_error = str(exc)
            state.status = "llm_unavailable"
            self.console.step("⚠️", "모델을 쓸 수 없습니다 — 진단과 참고 사례만 제시합니다.")
            self.console.detail(str(exc))
            return state

        if isinstance(report, BugReport):
            state.bug_report = report.render()
        else:
            try:
                state.bug_report = self._ask(
                    question + "\n\n핵심 정보만 담은 간결한 문단으로 답하십시오. "
                    "코드는 포함하지 마십시오.",
                    name="bug_report.fallback",
                    language=state.language,
                )
            except LLMUnavailable as exc:
                state.llm_error = str(exc)
                state.status = "llm_unavailable"
                self.console.step("⚠️", "모델을 쓸 수 없습니다 — 진단과 참고 사례만 제시합니다.")
                self.console.detail(str(exc))
                return state
        self.console.step("📝", "버그 리포트 작성")
        return state

    # ── 4. 메모리 검색 ─────────────────────────────────────────
    def memory_search_node(self, state: State) -> State:
        """과거 사례를 찾습니다.

        여기서 한 가지 요령이 있습니다. 버그 리포트를 그대로 질의어로 쓰지 않고,
        **저장할 때와 똑같은 템플릿으로 한 번 압축한 뒤** 검색합니다.
        저장된 문서와 질의의 표현 형태를 맞춰야 임베딩 거리가 의미를 갖습니다.

        **모델을 못 쓰는 축약 경로에서도 이 노드는 동작합니다.** 검색은 ``ChatBedrock``
        (Sonnet) 이 아니라 Titan 임베딩을 쓰므로 별도 한도를 갖기 때문입니다. 그때는
        압축을 못 하니 규칙 기반 재료를 그대로 질의어로 씁니다(정확도는 떨어집니다).

        이번 시도의 저장/병합 예약을 여기서 초기화합니다 — 이전 시도가 실패해서
        재시도로 돌아온 것이라면, 그 시도의 진단은 버리고 이번 시도 것만 남깁니다.
        """
        state.pending_memory_writes = []
        if state.llm_error:
            return self._degraded_memory_search(state)

        question = (
            "다음 버그 리포트를 나중에 찾아 쓰기 좋게 압축하십시오.\n"
            f"[버그 리포트] {state.bug_report}"
        )
        try:
            pattern = self._ask_structured(question, BugPattern, name="memory_query")
            if isinstance(pattern, BugPattern):
                # 저장·질의가 같은 렌더러를 통과해야 임베딩 거리가 의미를 갖습니다.
                state.memory_query = pattern.render()
                state.memory_pattern = pattern.pattern
                # 쿼리 확장(체크리스트 #3) — 세 면을 각각 던집니다. 한 문장으로 합쳐
                # 던지면 세 면이 평균되어 흐려지는데, 나눠 던지면 "증상으로 찾기" 와
                # "해결 방법으로 찾기" 가 각각 살아납니다. LLM 호출은 늘지 않습니다.
                state.memory_queries = [
                    state.memory_query,
                    pattern.pattern,
                    pattern.symptom,
                    pattern.cause_and_fix,
                ]
            else:
                state.memory_query = self._ask(
                    question + f"\n\n형식: {MEMORY_FORMAT}\n다른 말 없이 이 형식만 출력하십시오.",
                    name="memory_query.fallback",
                )
        except LLMUnavailable as exc:
            state.llm_error = str(exc)
            state.status = "llm_unavailable"
            self.console.step("⚠️", "모델을 쓸 수 없습니다 — 진단과 참고 사례만 제시합니다.")
            self.console.detail(str(exc))
            return self._degraded_memory_search(state)

        state.memory_hits = self.memory.search(
            state.memory_queries or state.memory_query, state.language
        )
        self._warn_if_search_failed()
        render_memory_hits(self.console, state.memory_hits)
        return state

    def _warn_if_search_failed(self) -> None:
        """검색이 실패했으면 알립니다.

        예전에는 ``MemoryStore.search`` 가 예외를 조용히 삼켜서, ``embed_query`` 누락으로
        검색이 **항상** 실패하던 것을 아무도 몰랐습니다. 메모리 주입이 이 프로젝트의 핵심
        기여인데 그것이 침묵 속에 꺼져 있으면 안 됩니다.
        """
        error = getattr(self.memory, "last_error", "")
        if error:
            self.console.step("⚠️", f"메모리 검색 실패 — 주입 없이 진행합니다: {error}")

    def _degraded_memory_search(self, state: State) -> State:
        """LLM 없이 검색합니다. **정확도가 낮다는 것을 반드시 알립니다.**

        저장된 문서는 ``# 패턴명 ## 증상 ### 원인과 해결`` 형태인데 여기서 만드는 질의어는
        원시 실패 출력이라 표현 형태가 어긋납니다. 즉 임베딩 거리가 평소만큼 의미를 갖지
        못합니다. 그래도 ``IndexError`` 같은 신호는 남아 있어 같은 계열 사례를 찾을 여지가
        있으므로, **참고용**임을 명시해 제시합니다. 정확도 낮은 결과를 정상 결과처럼 보이게
        하지 않는 것이 중요합니다.
        """
        target = Path(state.target_file).name if state.target_file else ""
        tail = _tail(state.test_output, 12)
        state.memory_query = " ".join(
            part for part in (state.language, target, tail) if part
        )
        # 규칙 기반 쿼리 확장 — LLM 없이도 확장이 동작해야 합니다.
        # 예외 이름(IndexError·KeyError …)은 그 자체가 강한 신호이고, 정확한 단어 일치라
        # BM25 가 특히 잘 잡습니다.
        exception_names = _exception_names(state.test_output)
        state.memory_queries = [
            q
            for q in (
                state.memory_query,
                " ".join(exception_names),
                f"{state.language} {target}".strip(),
            )
            if q and q.strip()
        ]
        state.memory_degraded_query = True
        try:
            state.memory_hits = self.memory.search(
                state.memory_queries or state.memory_query, state.language
            )
        except Exception as exc:
            # 여기는 최후의 보루입니다. 참고 사례를 못 찾는 것보다 나쁜 것은 아무 리포트도
            # 남기지 못하는 것입니다. (임베딩도 한도에 걸리면 이 경로가 열립니다)
            self.console.step("⚠️", f"메모리 검색도 실패했습니다: {type(exc).__name__}")
            state.memory_hits = []
        self._warn_if_search_failed()
        if state.memory_hits:
            render_memory_hits(self.console, state.memory_hits)
            self.console.detail("※ 축약 질의로 찾은 결과입니다 — 정확도가 평소보다 낮습니다.")
        else:
            self.console.step("🔎", "참고할 과거 사례가 없습니다.")
        return state

    # ── 5. 메모리 필터 ─────────────────────────────────────────
    def memory_filter_node(self, state: State) -> State:
        """충분히 가까운 것만 '같은 패턴' 으로 인정해 병합 대상에 올립니다."""
        state.memory_ids_to_update = [
            h.id for h in state.memory_hits if h.distance < self.memory.merge_threshold
        ]
        if state.memory_ids_to_update:
            self.console.detail(
                f"→ {len(state.memory_ids_to_update)}건이 기존 패턴과 동일 (병합 대상)"
            )
        return state

    # ── 6. 메모리 병합 ─────────────────────────────────────────
    def memory_modification_node(self, state: State) -> State:
        """기존 패턴에 이번 사례를 합칠 **예약**만 남깁니다. 실제 저장은 아닙니다.

        덮어쓰기가 아니라 누적 요약이라, 같은 패턴이 반복될수록 기록이 두꺼워집니다.
        대상이 여러 건이면 라우터가 이 노드로 되돌려 하나씩 처리합니다.

        **여기서 바로 ``self.memory.merge()`` 를 부르지 않습니다.** 이 시도가 실제로
        테스트를 통과시킬지는 아직 모릅니다 — 라우터 미등록처럼 한 번도 안 고쳐지는
        진단이 시도할 때마다 메모리에 쌓이는 문제가 있었습니다. 그래서 여기서는
        ``state.pending_memory_writes`` 에 예약만 남기고, 실제 병합은 이번 시도가
        테스트를 통과한 게 확인된 뒤(``main()``)에만 이뤄집니다.
        """
        memory_id = state.memory_ids_to_update.pop(0)
        prior = self.memory.get_document(memory_id)

        question = (
            "기존 버그 패턴 기록에 새 사례를 합치십시오. 두 사례를 아우르는 하나의 "
            "기록으로 만드십시오.\n"
            f"[기존 기록] {prior}\n"
            f"[새 사례] {state.memory_query or state.bug_report}"
        )
        pattern = self._ask_structured(
            question, BugPattern, name="memory_merge", memory_id=memory_id
        )
        if isinstance(pattern, BugPattern):
            merged = pattern.render()
            merged_name = pattern.pattern
        else:
            merged = self._ask(
                question + f"\n\n형식: {MEMORY_FORMAT}\n다른 말 없이 이 형식만 출력하십시오."
            )
            merged_name = ""

        state.pending_memory_writes.append(
            {
                "kind": "merge",
                "memory_id": memory_id,
                "summary": merged,
                "language": state.language,
                "target": Path(state.target_file).name,
                "pattern": merged_name,
            }
        )
        self.console.step("🧠", "기존 패턴과 유사 — 이번 시도가 통과하면 병합합니다")
        return state

    # ── 7. 메모리 신규 저장 ────────────────────────────────────
    def memory_generation_node(self, state: State) -> State:
        """처음 보는 패턴이면 새로 저장할 **예약**만 남깁니다. 발생 횟수 1 로 시작합니다.

        ``memory_modification_node`` 와 같은 이유로, 여기서 바로 ``self.memory.add()``
        를 부르지 않습니다 — 이번 시도가 실제로 테스트를 통과시켜야만 저장됩니다.
        """
        if state.memory_query:
            state.pending_memory_writes.append(
                {
                    "kind": "add",
                    "memory_id": None,
                    "summary": state.memory_query,
                    "language": state.language,
                    "target": Path(state.target_file).name,
                    "pattern": state.memory_pattern,
                }
            )
            self.console.step("💾", "새 패턴 후보 — 이번 시도가 통과하면 저장합니다")
        return state

    # ── 8. 수정안 생성 ─────────────────────────────────────────
    def code_update_node(self, state: State) -> State:
        """수정본을 만듭니다. **여기가 이 프로젝트의 핵심 기여입니다.**

        원본과 달리, 검색해 둔 과거 사례를 프롬프트에 실제로 주입합니다.
        메모리가 있으면 한 번에 맞출 확률이 올라가고, 그것이 재시도 횟수 감소로
        측정됩니다. 기여 근거는 ``report.json`` 의 ``memory_hits[].injected`` (무엇을
        넣었는가) 와 ``attempts`` (몇 번에 맞췄는가) 입니다.
        """
        state.attempts += 1

        # **임계값을 통과한 사례만** 주입합니다 (최대 2건).
        # 예전에는 아무도 통과하지 못하면 최상위 1건을 넣었지만, 그러면 관련 사례가 없어도
        # 항상 injected=True 가 되어 기여도를 판단할 근거가 사라집니다. 무관한 과거 버그가
        # 수정 방향을 잘못 끌 위험도 함께 없앴습니다. 주입이 굶고 있다면 폴백으로 덮지 말고
        # --merge-threshold 자체를 조정하는 것이 맞습니다.
        close = [h for h in state.memory_hits if h.distance < self.memory.merge_threshold]
        to_inject = close[:2]

        memory_block = ""
        if to_inject:
            for hit in to_inject:
                hit.injected = True
            joined = "\n".join(
                f"- ({hit.occurrences}회 발생) {hit.document}" for hit in to_inject
            )
            memory_block = f"\n[과거 유사 사례 — 참고하십시오]\n{joined}\n"
            self.console.detail(f"→ 과거 사례 {len(to_inject)}건을 수정 프롬프트에 주입")

        # 빌드가 깨진 경우와 테스트가 틀린 경우는 지시가 달라야 수렴합니다.
        if state.is_build_failure:
            objective = (
                "지금 출력은 테스트 실패가 아니라 **빌드/문법 에러**입니다. "
                "동작을 개선하기 전에 먼저 컴파일(파싱)이 되도록 만드십시오."
            )
        else:
            objective = "실패한 테스트가 통과하도록 소스를 수정하십시오."

        self.console.step("🩹", f"패치 제안 [시도 {state.attempts}/{state.max_attempts}]")

        prompt = (
            f"당신은 {state.language} 코드를 고치는 작업을 합니다.\n"
            f"{objective}\n\n"
            f"[파일] {Path(state.target_file).name}\n"
            + wrap_untrusted(state.current_source, "현재 소스") + "\n\n"
            + wrap_untrusted(_tail(state.test_output), "테스트 실패 출력") + "\n"
            f"{memory_block}\n"
            f"[지침]\n"
            f"- {self.adapter.style_hint}\n"
            "- 테스트 파일은 절대 수정 대상이 아닙니다. 위 소스 파일만 고칩니다.\n"
            "- 공개 함수의 이름과 시그니처는 그대로 유지하십시오.\n"
            "- 수정된 파일의 **전체 내용**만 출력하십시오.\n"
            "- 설명, 코드 펜스, 언어 표시를 붙이지 마십시오."
        )
        try:
            new_source = self._ask(
                prompt,
                name="code_update",
                attempt=state.attempts,
                injected=len(to_inject),
                build_failure=state.is_build_failure,
            )
        except LLMUnavailable as exc:
            # 수정안을 만들 수 없으므로 여기서 끝냅니다. 이미 검색해 둔 메모리는
            # 참고 사례로 리포트에 남습니다.
            state.llm_error = str(exc)
            state.status = "llm_unavailable"
            state.attempts -= 1  # 시도로 세지 않습니다 — 모델을 못 써서 시작조차 못 했습니다.
            self.console.step("⚠️", "모델을 쓸 수 없습니다 — 수정안을 만들지 못했습니다.")
            self.console.detail(str(exc))
            return state

        candidate = _strip_fence(new_source)

        # ── G9. 무관한 심볼을 지우거나 시그니처를 바꾸면 거부합니다 ──
        # 프롬프트로 "유지하십시오" 라고 부탁하는 것만으로는 막히지 않습니다.
        # 테스트가 덮지 않는 함수가 사라지면 아무도 모르게 통과하기 때문입니다.
        lost = self._lost_symbols(state.current_source, candidate)
        if lost:
            self.console.step(
                "🛡️", f"수정안 거부 — 무관한 심볼이 사라졌습니다: {', '.join(sorted(lost))} (G9)"
            )
            # 관측(체크리스트 #11) — 콘솔 한 줄로 흘러가면 여러 실행에 걸친 패턴을 볼 수
            # 없습니다. 시도별 거부 사유를 트레이스와 report.json 양쪽에 남깁니다.
            self._record_guardrail(state, "G9", sorted(lost))
            # 사본을 원래대로 두고 재시도로 넘깁니다. 재테스트가 다시 실패하므로
            # error_router 가 남은 시도만큼 다시 돌려 줍니다.
            state.new_source = state.current_source
            return state

        state.new_source = candidate
        return state

    def _flag_injection(self, state: State, *texts: str) -> None:
        """인젝션 의심 문구를 찾아 **기록만** 합니다 (체크리스트 #6, G11).

        차단하지 않는 이유: "무시하고" 같은 문구는 정상 코드 주석에도 나와 오탐에
        취약합니다. 진짜 방어는 이미 있는 구조적 가드레일입니다 — 인젝션이 성공해도
        테스트 파일은 못 고치고(G1), 승인 없이 원본에 못 쓰고(G6), 무관한 심볼을 지우면
        거부됩니다(G9). 여기서는 데이터/지시 분리(``wrap_untrusted``)와 탐지 기록만
        더합니다.
        """
        for text in texts:
            for name in detect_injection(text):
                if name not in state.injection_flags:
                    state.injection_flags.append(name)
                    self.console.step(
                        "🛡️", f"프롬프트 인젝션 의심 문구 감지: {name} (G11 — 차단하지 않고 기록만)"
                    )

    def _record_guardrail(self, state: State, guard_id: str, detail: list[str]) -> None:
        """가드레일이 수정안을 막았다는 사실을 기록합니다.

        ``report.json`` 의 ``guardrail_blocks`` 로 나가고, ``trace.log`` 에도 남습니다.
        LangSmith 가 켜져 있으면 같은 내용이 런 메타데이터로도 올라갑니다.
        """
        state.guardrail_blocks.append(
            {"guardrail": guard_id, "attempt": state.attempts, "detail": detail}
        )
        self.console.record(
            f"\n--- GUARDRAIL {guard_id} (시도 {state.attempts}) ---\n{', '.join(detail)}"
        )

    def _lost_symbols(self, before: str, after: str) -> set[str]:
        """수정 전에는 있었는데 수정 후 사라진 최상위 심볼을 돌려줍니다.

        판정할 수 없으면(파싱 실패 등) 빈 집합입니다 — 검증 장치가 수정을 막지 않습니다.
        """
        old = extract_symbols(self.adapter, before)
        new = extract_symbols(self.adapter, after)
        if old is None or new is None:
            return set()
        return old - new

    # ── 9. 패치 적용 ───────────────────────────────────────────
    def code_patching_node(self, state: State) -> State:
        """**샌드박스 사본의** 파일에 실제로 씁니다. 원본이 아닙니다.

        원본 노트북은 ``exec`` 로 메모리 안의 함수를 바꿔치기했지만, 여기서는 파일을 씁니다.
        그래야 언어에 무관하고, 검증도 진짜 빌드/테스트로 할 수 있습니다.

        **최종 실패해도 복원할 것이 없습니다.** 쓰기가 전부 사본에서만 일어나므로, 호출자가
        사본을 버리는 것(``shutil.rmtree``)이 곧 복원입니다. 원본은 애초에 한 번도 바뀌지
        않습니다 — **반쯤 고친 코드를 남기는 것이 가장 나쁜 결과**이기 때문입니다.
        """
        write_source(Path(state.target_file), state.new_source, state.newline)
        state.current_source = state.new_source
        return state


# ── 라우터 ──────────────────────────────────────────────────────

def error_router(state: State) -> str:
    """테스트 결과로 갈림길을 정합니다."""
    if not state.error:
        return "done"
    if state.attempts >= state.max_attempts:
        # 무한 루프 방지. 원본에는 이 제동장치가 없어서 계속 돌 수 있었습니다.
        return "exhausted"
    return "locate_node"


def locate_router(state: State) -> str:
    """대상을 못 찾으면 끝냅니다.

    모델을 못 쓰는 축약 경로에서는 **버그 리포트를 건너뛰고 메모리 검색으로 직행**합니다.
    리포트는 LLM 이 필요하지만 검색은 Titan 임베딩만 필요해 살아 있을 수 있기 때문입니다.
    """
    if state.status in ("failed", "llm_unavailable") and not state.target_file:
        return "done"
    if state.llm_error:
        return "memory_search_node"
    return "bug_report_node"


def bug_report_router(state: State) -> str:
    """리포트를 만들었으면 평소대로, 모델을 못 썼으면 축약 검색으로."""
    return "memory_search_node"


def memory_filter_router(state: State) -> str:
    """검색 결과가 있으면 필터로, 없으면 곧장 신규 저장으로.

    축약 경로에서는 **저장하지 않고 끝냅니다** — 요약을 만들 수 없으니 쓰레기를 저장하면
    이후 검색 품질만 망칩니다.
    """
    if state.llm_error:
        return "done"
    return "memory_filter_node" if state.memory_hits else "memory_generation_node"


def memory_generation_router(state: State) -> str:
    """병합할 것이 있으면 병합, 없으면 신규 저장."""
    return (
        "memory_modification_node"
        if state.memory_ids_to_update
        else "memory_generation_node"
    )


def memory_update_router(state: State) -> str:
    """병합 대상이 남아 있으면 자기 자신을 반복합니다."""
    return (
        "memory_modification_node"
        if state.memory_ids_to_update
        else "code_update_node"
    )


def code_update_router(state: State) -> str:
    """수정안을 만들었으면 적용으로, 모델을 못 썼으면 끝냅니다."""
    return "done" if state.status == "llm_unavailable" else "code_patching_node"


# ── 그래프 배선 ────────────────────────────────────────────────

def build_graph(nodes: HealingNodes):
    """노드 묶음을 받아 컴파일된 그래프를 돌려줍니다.

    ``nodes`` 를 주입받는 형태라, 가짜 LLM 을 꽂아 실호출 없이 배선만 검증할 수 있습니다.
    (AGENTS.md 규약)
    """
    builder = StateGraph(State)

    builder.add_node("test_execution_node", nodes.test_execution_node)
    builder.add_node("locate_node", nodes.locate_node)
    builder.add_node("bug_report_node", nodes.bug_report_node)
    builder.add_node("memory_search_node", nodes.memory_search_node)
    builder.add_node("memory_filter_node", nodes.memory_filter_node)
    builder.add_node("memory_modification_node", nodes.memory_modification_node)
    builder.add_node("memory_generation_node", nodes.memory_generation_node)
    builder.add_node("code_update_node", nodes.code_update_node)
    builder.add_node("code_patching_node", nodes.code_patching_node)

    builder.set_entry_point("test_execution_node")

    # 테스트 결과가 모든 흐름의 출발점입니다.
    builder.add_conditional_edges(
        "test_execution_node",
        error_router,
        {"done": END, "exhausted": END, "locate_node": "locate_node"},
    )
    builder.add_conditional_edges(
        "locate_node",
        locate_router,
        {
            "done": END,
            "bug_report_node": "bug_report_node",
            # 축약 경로 — 모델을 못 쓰면 리포트를 건너뛰고 검색으로 직행합니다.
            "memory_search_node": "memory_search_node",
        },
    )

    builder.add_conditional_edges(
        "bug_report_node",
        bug_report_router,
        {"memory_search_node": "memory_search_node"},
    )
    builder.add_conditional_edges(
        "memory_search_node",
        memory_filter_router,
        {
            "memory_filter_node": "memory_filter_node",
            "memory_generation_node": "memory_generation_node",
            # 축약 경로 — 요약을 못 만드니 저장하지 않고 끝냅니다.
            "done": END,
        },
    )
    builder.add_conditional_edges(
        "memory_filter_node",
        memory_generation_router,
        {
            "memory_modification_node": "memory_modification_node",
            "memory_generation_node": "memory_generation_node",
        },
    )
    builder.add_conditional_edges(
        "memory_modification_node",
        memory_update_router,
        {
            "memory_modification_node": "memory_modification_node",
            "code_update_node": "code_update_node",
        },
    )
    builder.add_edge("memory_generation_node", "code_update_node")

    builder.add_conditional_edges(
        "code_update_node",
        code_update_router,
        {"code_patching_node": "code_patching_node", "done": END},
    )
    # 고쳤다고 믿지 않고 반드시 다시 테스트합니다. 판정 기준은 언제나 테스트입니다.
    builder.add_edge("code_patching_node", "test_execution_node")

    return builder.compile()


# ── CLI 진입점 (구 heal.py) ────────────────────────────────────

def load_dotenv_if_present() -> None:
    """``.env`` 를 읽어 환경변수로 올립니다.

    이 과정의 관례가 ``.env`` 에 Bedrock 자격증명을 두는 것(``.env.example``)이라,
    그 파일만 채우면 바로 돌아가야 합니다. python-dotenv 가 없으면 직접 파싱합니다.
    이미 셸에 설정된 값은 덮어쓰지 않습니다.

    **프로젝트 폴더부터 위로 올라가며 찾습니다.** 이 프로젝트는 여러 실습 폴더를 담은
    저장소 안에 있고, ``.env`` 를 저장소 루트에 한 번만 두는 것이 자연스럽습니다.
    프로젝트 폴더만 보면 자격증명이 있는데도 못 찾아 ``NoCredentialsError`` 가 납니다.
    가까운 것이 우선이므로 프로젝트 폴더의 ``.env`` 를 먼저 씁니다.
    """
    project_root = Path(__file__).resolve().parents[1]
    for base in (project_root, *project_root.parents):
        env_path = base / ".env"
        if env_path.exists():
            _load_env_file(env_path)
            return


def _load_env_file(env_path: Path) -> None:
    try:
        from dotenv import load_dotenv

        load_dotenv(env_path, override=False)
        return
    except ImportError:
        pass
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip("\"'")
        if key and value and key not in os.environ:
            os.environ[key] = value


# 메모리는 실행 디렉터리가 아니라 이 프로젝트 루트 아래에 모읍니다.
# 여러 샘플을 돌려도 하나의 기억이 쌓여야 "학습" 이 되기 때문입니다.
# (이 파일이 src/ 아래로 한 단계 더 들어왔으므로 parents[1] 이 프로젝트 루트입니다.)
MEMORY_DIR = Path(__file__).resolve().parents[1] / "chroma_db"

# 원복 기능은 두지 않습니다. ``--apply`` 로 실행한 것은 사용자가 이미 승인한 변경이고,
# 되돌리려면 git 이 있습니다(``git diff`` · ``git checkout --``). 기본 동작이 애초에 원본을
# 건드리지 않으므로 실수로 덮어쓸 경로도 없습니다. 그래서 백업·적용 이력·원복 확인 화면을
# 모두 만들지 않습니다.


def _user_path(path: Path, sandbox: Path, workdir: Path) -> Path:
    """샌드박스 안의 경로를 사용자가 아는 원본 경로로 되돌립니다.

    모든 작업은 임시 사본에서 일어나므로 상태에 담긴 경로는 임시 폴더를 가리킵니다.
    실행이 끝나면 그 폴더는 지워지므로, 화면과 리포트에 그대로 내면 사용자가 쓸 수 없는
    경로가 남습니다. 사본 안이 아니면 그대로 돌려줍니다.
    """
    try:
        return workdir / path.resolve().relative_to(sandbox.resolve())
    except ValueError:
        return path


def warn_if_heal_tracked(workdir: Path, console: Console) -> None:
    """산출물 폴더가 대상 저장소에 추적되지 않은 파일로 남을 상황이면 알립니다.

    ``.heal/`` 은 검수할 diff 가 대상 폴더에 있어야 하므로 **대상 디렉터리 안에** 만듭니다.
    이 저장소는 자체 ``.gitignore`` 에 ``.heal/`` 을 넣어 두었지만, 사용자가 자기 프로젝트를
    대상으로 실행하면 그 규칙이 없어 커밋 후보로 잡힙니다. 우리가 남긴 산출물이 사용자의
    커밋을 더럽히지 않도록, 조용히 두지 않고 한 줄 안내합니다.

    git 이 없거나 저장소가 아니면 아무 말도 하지 않습니다.
    """
    try:
        inside = subprocess.run(
            ["git", "-C", str(workdir), "rev-parse", "--is-inside-work-tree"],
            capture_output=True, text=True, timeout=10,
        )
        if inside.returncode != 0 or inside.stdout.strip() != "true":
            return
        ignored = subprocess.run(
            ["git", "-C", str(workdir), "check-ignore", "-q", ".heal"],
            capture_output=True, text=True, timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return
    if ignored.returncode != 0:  # 0 = 무시됨
        console.detail(".heal/ 이 git 에 추적됩니다 — .gitignore 에 `.heal/` 을 추가하십시오.")


def _prompt_for_path(console: Console) -> Path | None:
    """인수 없이 실행했을 때 경로를 대화형으로 입력받습니다.

    최대 3회 재시도합니다. 그 뒤에도 못 받으면 호출자가 exit 3 으로 끝냅니다.
    """
    for _ in range(3):
        try:
            raw = input("📁 검수할 폴더 또는 파일을 입력하세요\n   > ").strip()
        except EOFError:
            # isatty() 가 True 를 보고했더라도 입력이 그새 끊길 수 있습니다.
            return None
        if not raw:
            continue
        path = Path(raw).expanduser().resolve()
        if path.exists():
            return path
        console.log(f"경로를 찾을 수 없습니다: {path}")
    return None


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="agent.py",
        description="디렉터리 하나를 받아 깨진 테스트를 스스로 고치는 에이전트입니다.",
    )
    parser.add_argument("workdir", nargs="?", type=Path, help="대상 디렉터리")
    parser.add_argument(
        "--max-attempts", type=int, default=2, help="수정 재시도 횟수 (기본 2)"
    )
    parser.add_argument(
        "--cross-language",
        action="store_true",
        help="언어 격리를 풀고 다른 언어의 패턴까지 참고합니다. (전이 실험용)",
    )
    parser.add_argument(
        "--merge-threshold",
        type=float,
        default=DEFAULT_MERGE_THRESHOLD,
        help=f"같은 패턴으로 볼 거리 임계값 (기본 {DEFAULT_MERGE_THRESHOLD})",
    )
    parser.add_argument(
        "--test-cmd", help="테스트 명령 override. 모노레포처럼 관례를 벗어난 경우에 씁니다."
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="검증된 수정안을 원본 파일에 실제로 적용합니다. "
             "이 플래그가 없으면 제안만 하고 원본은 건드리지 않습니다.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="기본 동작(제안만)과 같습니다. 아무 동작도 바꾸지 않는 별칭이며, "
             "CI 스크립트에서 의도를 드러내고 싶을 때 씁니다. (G8)",
    )
    parser.add_argument("--lang", choices=sorted(ADAPTERS), help="언어 자동 감지 override")
    parser.add_argument(
        "--model",
        help="Bedrock 모델 override. 별칭(sonnet · haiku) 또는 전체 모델 ID. "
             "기본값은 AGENTS.md 고정 스택인 Sonnet 4.5 입니다. "
             "일일 토큰 한도에 걸린 날 더 가벼운 모델로 검증을 이어갈 때 씁니다.",
    )
    parser.add_argument(
        "--trace",
        dest="trace",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="LangSmith 추적을 이 실행에서만 켜거나(--trace) 끕니다(--no-trace). "
             "주지 않으면 .env 의 LANGSMITH_TRACING 을 따릅니다. "
             "켜면 프롬프트와 소스 전문이 외부(LangSmith)로 전송됩니다.",
    )
    parser.add_argument("--stats", action="store_true", help="누적 버그 패턴 리포트만 출력")
    parser.add_argument("--quiet", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None, llm=None, memory=None) -> int:
    """``llm`` 과 ``memory`` 는 주입용입니다.

    AGENTS.md 규약에 따라 가짜 모델을 꽂아 실호출 없이 배선과 가드레일을 검증합니다.
    실제 실행에서는 둘 다 ``None`` 이므로 노드가 Bedrock 모델을, 아래 코드가 실제
    ``MemoryStore`` 를 지연 생성합니다.

    ``memory`` 주입 지점이 필요한 이유는 임베딩이 실호출이라는 데 있습니다. 예전에는
    ``--no-memory`` 로 메모리를 꺼서 테스트를 오프라인으로 돌렸지만, 그 옵션을 없앤 뒤에는
    가짜 임베딩을 꽂은 저장소를 여기로 넘겨 같은 목적을 달성합니다.
    """
    load_dotenv_if_present()
    args = parse_args(argv)
    console = Console(quiet=args.quiet)

    # 관측(체크리스트 #11). 기본은 꺼져 있고, .env 에 LANGSMITH_TRACING 과
    # LANGSMITH_API_KEY 가 둘 다 있을 때만 켜집니다. --trace / --no-trace 를 주면 그 실행에서만
    # .env 를 덮어씁니다(훅처럼 자동 실행되는 경로에서 끄기 위함). 자세한 사유는 src/tracing.py 참고.
    trace_enabled = configure_tracing(console, override=args.trace)

    # ── --stats: 누적 리포트만 출력하고 끝냅니다 ────────────────
    if args.stats:
        store = memory if memory is not None else MemoryStore(MEMORY_DIR)
        render_stats(console, store.stats())
        return 0

    # ── 1단계: 인수 없이 실행 — 경로를 물어봅니다 ───────────────
    # 사용자는 플래그를 모릅니다. 인수 없이 쳐도 끝까지 갈 수 있어야 합니다.
    if args.workdir is None:
        if not sys.stdin.isatty():
            # 파이프·CI 에서 물어보면 멈춥니다. 지금처럼 즉시 종료합니다.
            console.log("대상 디렉터리를 지정하십시오. 예: python -m src.agent ./data/samples/py-index")
            return EXIT_PRECONDITION

        path = _prompt_for_path(console)
        if path is None:
            console.log("경로를 받지 못했습니다.")
            return EXIT_PRECONDITION
    else:
        path = args.workdir.resolve()
        if not path.exists():
            console.log(f"경로를 찾을 수 없습니다: {path}")
            return EXIT_PRECONDITION

    # ── 1단계: 경로 하나 — 폴더 또는 파일 ─────────────────────
    specified_file: Path | None = None
    if path.is_file():
        specified_file = path
        root = find_project_root(path)
        if root is None:
            console.step("❌", f"{path} 의 언어를 알아낼 프로젝트 루트를 찾지 못했습니다.")
            return EXIT_PRECONDITION
        workdir = root
    elif path.is_dir():
        workdir = path
    else:
        console.log(f"디렉터리도 파일도 아닙니다: {path}")
        return EXIT_PRECONDITION

    # ── 2단계: 언어 감지 ───────────────────────────────────────
    try:
        adapter = ADAPTERS[args.lang] if args.lang else detect_language(workdir)
    except LanguageDetectionError as exc:
        console.step("❌", str(exc))
        return EXIT_PRECONDITION
    if args.lang:
        marker = "--lang 지정"
    else:
        marker = next((m for m in adapter.markers if (workdir / m).exists()), "확장자 최빈값")
    console.step("🔍", f"언어 감지: {adapter.name} ({marker})")

    # 파일을 지정했으면 여기서 검증합니다. 언어(adapter)를 알아야 판단할 수 있습니다.
    if specified_file is not None:
        rel_parts = specified_file.relative_to(workdir).parts
        if any(part in EXCLUDED_DIRS for part in rel_parts):
            console.step(
                "❌", f"{specified_file} 는 제외 경로 안에 있어 수정할 수 없습니다. (G3)"
            )
            return EXIT_PRECONDITION
        if adapter.is_test_file(specified_file):
            console.step(
                "❌",
                f"{specified_file.name} 는 수정할 수 없습니다. (G1: 테스트는 성공 기준입니다)",
            )
            return EXIT_PRECONDITION
        if not adapter.is_source_file(specified_file):
            console.step("❌", f"{specified_file.name} 는 고칠 수 있는 소스 파일이 아닙니다.")
            return EXIT_PRECONDITION

    # ── 3단계: 툴체인 preflight ───────────────────────────────
    # 그래프에 들어가기 전에 막습니다. 툴체인이 없으면 테스트는 무조건 실패하고,
    # 에이전트는 멀쩡한 코드를 계속 고치려 들면서 토큰만 태웁니다.
    ok, info = preflight(adapter)
    if not ok:
        console.step("❌", info)
        return EXIT_PRECONDITION
    console.detail(f"툴체인: {info}")

    # ── 4단계: 스캔 집계 ───────────────────────────────────────
    # 테스트는 이 시스템의 성공 판정 기준이자 명세입니다. 없으면 "고쳤다" 를 정의할 수 없습니다.
    _, test_count = scan_workdir(adapter, workdir)
    if not test_count:
        console.step("❌", "테스트 파일이 없습니다. 이 에이전트는 테스트를 성공 기준으로 씁니다.")
        return EXIT_PRECONDITION
    # 사용자가 --file 로 고칠 대상을 좁혀도 test_cmd 는 여전히 프로젝트 전체 테스트를
    # 돌립니다(성공 판정은 항상 전체 테스트 기준) — 그래서 이 개수는 specified_file 과
    # 무관하게 항상 워크디렉터리 전체의 테스트 파일 수입니다.
    console.step("📂", f"테스트 파일 {test_count}개를 실행합니다.")

    test_cmd = args.test_cmd.split() if args.test_cmd else resolve_test_cmd(adapter, workdir)

    # ── 5단계: 샌드박스 준비 ───────────────────────────────────
    # **원본을 절대 건드리지 않기 위한 장치입니다.**
    # 에이전트는 사본에서 고치고 사본에서 테스트를 돌립니다. 그래서 사용자에게
    # diff 를 낼 때 이미 "이 패치는 테스트를 통과한다" 가 검증된 상태입니다.
    # 원본에 쓰는 것은 사용자가 --apply 로 승인했을 때, 맨 마지막 한 번뿐입니다.
    # EXCLUDED_DIRS 는 스캔·수정 대상에서 이미 빠지는 경로라 사본에도 없어도 됩니다
    # (node_modules 같은 거대 폴더를 매번 복사하지 않기 위함).
    # 모노레포 안이면 workdir 를 리포지토리 루트 기준 상대 경로 그대로 사본에도
    # 재현합니다. 그래야 workdir 밖(리포지토리 루트)에 있는 파일을 상대 경로로
    # 참조하는 테스트(예: ``.github/workflows/*.yml``)가 사본에서도 같은 자리에서
    # 그 파일을 찾을 수 있습니다.
    git_root = find_git_root(workdir)
    workdir_resolved = workdir.resolve()
    if git_root and git_root != workdir_resolved:
        sandbox_rel = workdir_resolved.relative_to(git_root)
    else:
        git_root = None
        sandbox_rel = Path(workdir.name)

    sandbox_root = Path(tempfile.mkdtemp(prefix="selfheal-"))
    sandbox = sandbox_root / sandbox_rel
    sandbox.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(
        workdir,
        sandbox,
        ignore=shutil.ignore_patterns(".pytest_cache", *EXCLUDED_DIRS),
    )
    console.step("📦", "샌드박스 사본에서 작업합니다 (원본 불변)")

    # node_modules 는 EXCLUDED_DIRS 라 위 copytree 에 없습니다. 스캔·수정 대상에서도
    # 이미 빠져 있으니 통째로 복사할 필요는 없지만, 없으면 npm test 가 부트스트랩
    # 단계에서 죽어 진단이 완전히 엉뚱한 방향으로 새므로 원본을 그대로 가리키는
    # 링크만 겁니다.
    node_modules = workdir / "node_modules"
    if node_modules.is_dir():
        how = link_or_copy_dir(node_modules, sandbox / "node_modules")
        console.detail(f"node_modules: 원본 재사용 ({how})")

    # 리포지토리 루트 바로 아래의 **점(.) 디렉터리·파일만** 형제로 재현합니다
    # (``.github`` 같은 CI 설정). ``frontend/``·``deploy/`` 같은 다른 패키지는
    # workdir 밖이라 범위 밖이므로 절대 끌어오지 않습니다 — G2(작업 디렉터리
    # 밖은 손대지 않는다)가 지키는 경계를 샌드박스 준비 단계에서도 그대로 지킵니다.
    if git_root:
        for entry in git_root.iterdir():
            if entry.name == ".git" or not entry.name.startswith("."):
                continue
            target = sandbox_root / entry.name
            if target.exists():
                continue
            if entry.is_dir():
                # node_modules 와 달리 화면에는 안 찍습니다 — 저장소 루트에 있는
                # 점 폴더가 여러 개면 사용자에게 무의미한 줄만 늘어납니다.
                link_or_copy_dir(entry, target)
            elif entry.is_file():
                shutil.copy2(entry, target)

    try:
        # ── 5. 구성 요소 조립 ──────────────────────────────────
        if memory is None:
            memory = MemoryStore(
                MEMORY_DIR,
                merge_threshold=args.merge_threshold,
                cross_language=args.cross_language,
            )
        model_id = resolve_model_id(args.model)
        # G12(커밋 안 된 파일만 수정) 판단용입니다. 위쪽의 ``git_root`` 변수는
        # "샌드박스에 리포지토리 루트를 재현해야 하는가" 만 따지느라 workdir 가
        # 곧 리포지토리 루트인 흔한 경우엔 일부러 None 으로 지워지므로, 그것과는
        # 별개로 다시 찾습니다.
        guardrail_git_root = find_git_root(workdir)
        if guardrail_git_root:
            dirty_count = count_dirty_files(workdir, guardrail_git_root)
            if dirty_count is not None:
                console.step(
                    "📝",
                    f"변경 내역 {dirty_count}개 (커밋 안 됨) — 수정 대상은 이 안에서만 고릅니다",
                )
        nodes = HealingNodes(
            adapter=adapter,
            memory=memory,
            console=console,
            llm=llm,
            model_id=model_id,
            real_workdir=workdir,
            git_root=guardrail_git_root,
        )
        if model_id != LLM_MODEL_ID:
            # 기본이 아닌 모델로 낸 결과를 기본 스택의 결과로 착각하면 안 됩니다.
            console.step("🔀", f"모델 override: {model_id}")
        graph = build_graph(nodes)

        state = State(
            workdir=str(sandbox),
            language=adapter.name,
            test_cmd=test_cmd,
            max_attempts=args.max_attempts,
            model_id=model_id,
        )
        if specified_file is not None:
            # 1단계에서 사용자가 지정한 파일 — 7단계의 "사용자 지정 우선" 규칙과
            # 연결됩니다. locate_node 는 이 값을 그대로 존중합니다.
            rel = specified_file.relative_to(workdir)
            sandbox_target = sandbox / rel
            state.target_file = str(sandbox_target)
            state.original_source = sandbox_target.read_text(encoding="utf-8")
            state.current_source = state.original_source
            # locate_node 가 이 경로에서는 줄바꿈을 감지하지 않으므로 여기서 채웁니다.
            state.newline = detect_newline(sandbox_target)

        started = time.time()
        # 예상 못 한 예외가 나도 산출물은 남겨야 합니다. traceback 만 남기고 죽으면
        # 사용자에게 아무 근거가 없고, 훅처럼 자동 실행되는 경로에서는 더 나쁩니다.
        result = graph.invoke(
            state,
            {
                # 노드 하나당 한 스텝이므로, 재시도 횟수에 비례해 넉넉히 잡습니다.
                "recursion_limit": args.max_attempts * 15 + 20,
                # 관측 — LangGraph 도 Runnable 이라 이 실행 하나가 트레이스 하나가 됩니다.
                # 추적이 꺼져 있으면 이 값들은 무해하게 무시됩니다.
                "run_name": f"selfheal:{workdir.name}",
                "tags": ["selfheal", adapter.name],
                "metadata": {
                    "language": adapter.name,
                    "test_cmd": " ".join(test_cmd),
                    "max_attempts": args.max_attempts,
                    "merge_threshold": args.merge_threshold,
                    "cross_language": args.cross_language,
                    "apply": bool(args.apply),
                },
            },
        )
        state = State.model_validate(result)
        elapsed = round(time.time() - started, 1)

        # ── 6. 판정과 적용 ─────────────────────────────────────
        if state.status == "running":
            state.status = "failed" if state.error else "fixed"
        if state.llm_error and state.status not in ("fixed", "nothing_to_fix"):
            # 노드가 축약 경로로 전환했으면 판정도 그것을 따릅니다.
            state.status = "llm_unavailable"

        commit_pending_memory_writes(memory, state, console)

        diff = ""
        applied_to: Path | None = None

        if state.status == "fixed" and state.target_file:
            patched = Path(state.target_file)
            new_source = patched.read_text(encoding="utf-8")
            rel = patched.relative_to(sandbox.resolve())
            diff = make_diff(state.original_source, new_source, str(rel))

            if args.apply:
                # 승인받은 경우에만 원본에 씁니다.
                # 줄바꿈을 원본 방식으로 되돌려 씁니다 — 그러지 않으면 CRLF 프로젝트에서
                # 승인 한 번에 파일 전체의 줄바꿈이 바뀝니다.
                target = workdir / rel
                write_source(target, new_source, state.newline)
                applied_to = target
            else:
                # 기본 동작: 제안만 하고 사용자 판단을 기다립니다.
                state.status = "proposed"

            # 리포트에는 사본 경로가 아니라 사용자가 아는 경로를 씁니다.
            state.target_file = str(workdir / rel)
        elif state.target_file:
            # 고치지 못한 경우(축약 경로·실패)도 마찬가지입니다. 샌드박스는 곧 지워지므로
            # "진단된 대상" 이 임시 경로면 사용자가 그 파일을 열어 볼 수도 없습니다.
            # 위 분기는 패치된 사본을 읽어야 하므로 그 뒤에 둡니다.
            state.target_file = str(_user_path(Path(state.target_file), sandbox, workdir))

        render_result(console, state, diff)

        # 산출물은 원본 디렉터리에 남깁니다. 검수할 diff 가 여기 있어야 하기 때문입니다.
        out = write_artifacts(
            workdir,
            state,
            diff,
            console,
            extra={
                "duration_sec": elapsed,
                "applied": applied_to is not None,
                "sandbox": True,
                # 이 실행에서 프롬프트가 외부(LangSmith)로 나갔는지. 리포트만 보고도
                # 알 수 있어야 합니다.
                "trace_enabled": trace_enabled,
            },
        )
        console.log("")
        console.detail(f"산출물: {out}")
        warn_if_heal_tracked(workdir, console)
        if state.status == "proposed":
            console.detail(f"검수용 패치: {out / 'patch.diff'}")
            if diff:
                html_path = out / "patch.html"
                html_path.write_text(render_diff_html(diff), encoding="utf-8")
                try:
                    webbrowser.open(html_path.resolve().as_uri())
                except Exception:
                    console.detail("브라우저 자동 실행 실패 — patch.html 을 직접 열어 주세요.")
        elif state.status == "nothing_to_fix":
            # 애초에 고칠 게 없었던 경우입니다. 실패했을 때만 리포트를 띄우면
            # "정말 전부 통과했는지" 를 개별 테스트 단위로 확인할 길이 없어서,
            # 통과했을 때도 같은 방식으로 결과 리포트를 남깁니다.
            report_path = out / "report.html"
            report_path.write_text(
                render_pass_report_html(
                    " ".join(state.test_cmd), extract_test_cases(adapter, state.test_output)
                ),
                encoding="utf-8",
            )
            console.detail(f"결과 리포트: {report_path}")
            try:
                webbrowser.open(report_path.resolve().as_uri())
            except Exception:
                console.detail("브라우저 자동 실행 실패 — report.html 을 직접 열어 주세요.")
        elif state.status == "failed" and state.target_file:
            # 자동 수정은 실패했지만, 이 파일에 그것과 무관하게 언어 공통적으로
            # 흔한 결함(null·경계값 등)이 있는지 정도는 참고용으로 훑어 줍니다.
            # 업무 로직 판단(예: 이 실패의 진짜 원인이 다른 파일에 있는지)은
            # 하지 않습니다 — 적용도 자동으로 하지 않고 제안만 남깁니다.
            console.step("🔎", "자동 수정은 실패했습니다 — 일반 결함이 있는지만 참고용으로 훑습니다.")
            try:
                target_path = Path(state.target_file)
                source = target_path.read_text(encoding="utf-8")
                prompt = build_suggestion_prompt(state.language, target_path.name, source)
                suggestion = nodes._ask(prompt, name="general_defect_suggestion")

                # 무엇을 통과시켜야 하는지(명세)도 같이 보여줍니다. 절대 수정 대상이
                # 아니라 참고용입니다 — locate_targets 와 달리 테스트 파일만 남깁니다.
                test_files = []
                for test_path in locate_test_files(adapter, state.test_output, sandbox)[:2]:
                    try:
                        real_test_path = _user_path(test_path, sandbox, workdir)
                        test_files.append(
                            (real_test_path.name, real_test_path.read_text(encoding="utf-8"))
                        )
                    except OSError:
                        continue

                # 마지막 시도의 테스트 케이스별 통과/실패 전체 목록. "테스트 파일 N개"
                # 와는 다른 숫자입니다 — 파일 하나에 테스트가 여러 개 있을 수 있습니다.
                test_cases = extract_test_cases(adapter, state.test_output)

                notice_path = out / "notice.html"
                notice_path.write_text(
                    render_suggestion_html(
                        target_path.name,
                        source,
                        suggestion,
                        state.attempt_log,
                        test_files,
                        test_cases,
                    ),
                    encoding="utf-8",
                )
                console.detail(f"결함 제안 리포트: {notice_path}")
                try:
                    webbrowser.open(notice_path.resolve().as_uri())
                except Exception:
                    console.detail("브라우저 자동 실행 실패 — notice.html 을 직접 열어 주세요.")
            except LLMUnavailable:
                # 자동 수정도 이미 실패한 상태라 모델을 또 못 쓰면 조용히 넘어갑니다 —
                # exit code 나 기존 실패 로그는 그대로 유지됩니다.
                pass
            except OSError:
                pass

    except Exception as exc:
        # **예상 못 한 예외에도 근거를 남깁니다.** 예전에는 여기서 traceback 만 남기고
        # 죽어 `.heal/` 에 아무것도 쓰이지 않았습니다. 관측을 강조하면서 정작 실패하면
        # 아무것도 안 남는 모순을 없앱니다. 훅처럼 자동 실행되는 경로에서는 더 중요합니다.
        # 축약 경로가 이미 "모델 불가" 로 판정했다면 그것을 지킵니다. 뒤따르는 2차 실패가
        # 더 정보량이 많은 1차 원인을 일반 실패로 덮어쓰면 사용자가 원인을 오해합니다.
        primary_llm_failure = bool(state.llm_error) or isinstance(exc, LLMUnavailable)
        state.llm_error = state.llm_error or f"{type(exc).__name__}: {exc}"
        state.status = "llm_unavailable" if primary_llm_failure else "failed"
        console.step("❌", f"실행이 중단되었습니다 — {type(exc).__name__}")
        console.detail(str(exc))
        render_result(console, state, "")
        out = write_artifacts(
            workdir,
            state,
            "",
            console,
            extra={
                "applied": False,
                "sandbox": True,
                "trace_enabled": trace_enabled,
                "crashed": True,
            },
        )
        console.detail(f"산출물: {out}")
        return STATUS_TO_EXIT.get(state.status, 1)

    finally:
        # 샌드박스는 반드시 치웁니다. 성공하든 실패하든 임시물이 남으면 안 됩니다.
        shutil.rmtree(sandbox_root, ignore_errors=True)

    return STATUS_TO_EXIT.get(state.status, 1)


if __name__ == "__main__":
    sys.exit(main())
