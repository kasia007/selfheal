"""언어 어댑터 — 이 프로젝트가 원본 노트북과 갈라지는 지점입니다.

원본(self_healing_code.ipynb)은 ``inspect.getsource`` 와 ``exec`` 에 의존해서
파이썬 함수만 고칠 수 있었습니다. 여기서는 그 두 가지를 버리고
**"파일을 고치고 테스트를 다시 돌린다"** 로 바꿉니다.

그러면 언어마다 달라지는 것은 결국 네 가지뿐입니다.

1. 이 디렉터리가 무슨 언어인가 (마커 파일)
2. 툴체인이 깔려 있는가 (probe 명령)
3. 테스트를 어떻게 돌리는가 (test 명령)
4. 실패 출력에서 "고쳐야 할 파일"을 어떻게 뽑는가 (스택트레이스 정규식)

새 언어를 추가하려면 아래 ``ADAPTERS`` 에 항목 하나만 더 넣으면 됩니다.
그래프 구조도 노드도 건드릴 필요가 없습니다.
"""

from __future__ import annotations

import ast
import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class LanguageAdapter:
    """한 언어를 고치는 데 필요한 모든 지식을 담은 값 객체입니다."""

    name: str
    """언어 이름. 메모리 metadata 에도 이 값이 그대로 들어갑니다."""

    extensions: tuple[str, ...]
    """소스 파일 확장자. 수정 대상 화이트리스트 판정에 씁니다."""

    markers: tuple[str, ...]
    """이 파일이 디렉터리에 있으면 해당 언어로 봅니다. (go.mod, package.json ...)"""

    probe_cmd: list[str]
    """툴체인 존재 확인용 명령. 실패하면 그래프에 진입조차 하지 않습니다."""

    test_cmd: list[str]
    """기본 테스트 명령. package.json 처럼 프로젝트가 따로 정의하면 override 됩니다."""

    test_file_patterns: tuple[str, ...]
    """테스트 파일 판별용 glob. **여기 걸리는 파일은 절대 수정하지 않습니다.**
    열어 주면 LLM 이 테스트를 고쳐서 통과시켜 버리기 때문입니다."""

    trace_patterns: tuple[str, ...] = ()
    """테스트 실패 출력에서 (파일, 줄) 을 뽑는 정규식. group(1)=경로, group(2)=줄번호."""

    style_hint: str = ""
    """수정 프롬프트에 끼워 넣을 언어별 지침입니다."""

    compile_markers: tuple[str, ...] = ()
    """이 문자열이 실패 출력에 있으면 '테스트 실패' 가 아니라 '빌드 실패' 로 봅니다."""

    symbol_patterns: tuple[str, ...] = ()
    """최상위 심볼(함수·클래스·타입) 이름을 뽑는 정규식. group(1)=이름.

    G9(수정안 구조 검증)에 씁니다. 파이썬은 정규식 대신 ``ast`` 를 쓰므로 비어 있습니다."""

    failure_section_marker: str = ""
    """실패 출력에서 '진짜 실패 구간' 이 시작하는 지점을 찾는 정규식.

    pytest·npm test 처럼 여러 테스트를 한 번에 돌리는 러너는, **통과한** 테스트가
    일부러 예외를 유발해 검증하는 과정(예: ``assert.throws``)에서도 스택트레이스를
    출력에 남길 수 있습니다. ``locate_targets`` 가 출력 전체를 훑으면 그 "통과한
    테스트의 부산물"을 실제 실패 지점으로 착각할 수 있습니다. 이 마커가 있으면
    그 지점부터만 훑고, 없으면(빈 문자열) 예전처럼 출력 전체를 훑습니다."""

    error_summary_line_pattern: str = ""
    """로그 한 줄 요약(``_one_line``)에서 우선적으로 찾을, '진짜 에러 메시지 줄' 정규식.

    파이썬 트레이스백은 마지막 줄이 곧 에러 메시지("IndexError: ...")라 출력의
    맨 끝 줄을 그대로 쓰면 됩니다. 하지만 Node.js 는 에러 객체를 여러 줄짜리
    속성 목록으로 찍고 그 목록이 ``}`` 한 글자짜리 줄로 끝나는 경우가 흔해서,
    "맨 끝 줄" 을 그대로 쓰면 의미 없는 ``}`` 가 요약으로 남습니다. 언어마다
    실제 에러 메시지가 어디 있는지가 다르므로 언어별로 이 정규식을 따로 둡니다."""

    test_case_pattern: str = ""
    """테스트 러너가 **개별 테스트마다** 찍는 한 줄(통과/실패 기호 + 이름)을 뽑는
    정규식. group(1)=기호, group(2)=이름. "테스트 파일 N개" 는 몇 개를 도는지일
    뿐, 실제로 몇 개의 개별 테스트가 통과/실패했는지는 이걸로만 알 수 있습니다.
    러너마다 기본 출력 형식이 달라(파이썬 ``pytest -q`` 는 기본은 점(.)만 찍고
    이름이 안 나옴, go test 도 기본은 개별 테스트를 안 보여줌) 지금은 이미
    기본 출력에 이름이 찍히는 javascript(node --test)만 지원합니다."""

    def is_test_file(self, path: Path) -> bool:
        """테스트 파일이면 True. 수정 화이트리스트에서 제외됩니다."""
        return any(path.match(pat) for pat in self.test_file_patterns)

    def is_source_file(self, path: Path) -> bool:
        """고쳐도 되는 소스 파일인지 판정합니다."""
        return path.suffix in self.extensions and not self.is_test_file(path)

    def is_build_failure(self, output: str) -> bool:
        """빌드가 깨진 것인지 테스트가 틀린 것인지 구분합니다.

        Go/Java 같은 컴파일 언어에서 LLM 이 문법을 깨면 컴파일 에러가 납니다.
        이때 프롬프트를 "테스트를 통과시켜라" 가 아니라 "먼저 컴파일되게 하라" 로
        바꿔야 수렴합니다. 파이썬만 다룰 때는 보이지 않던 문제입니다.

        **판정은 이번에 다루는 실패 구간에만 한정합니다** (``_failure_section``).
        ``output`` 은 전체 테스트 스위트를 한 번에 돌린 결과라, 이번 대상과
        무관한 다른 파일에 컴파일 에러가 있어도 그 문자열이 그대로 섞여 들어옵니다.
        전체를 다 보면 "이번 대상이 컴파일 에러다" 가 아니라 "이 프로젝트 어딘가에
        컴파일 에러가 있다" 를 판정하게 되어, 무관한 대상에도 잘못된 지침
        ("먼저 컴파일되게 하라")을 얹는 오해를 일으킵니다.
        """
        scoped = _failure_section(self, output)
        return any(marker in scoped for marker in self.compile_markers)


# ── 어댑터 레지스트리 ────────────────────────────────────────────────
# 새 언어 지원 = 이 dict 에 항목 하나 추가.

ADAPTERS: dict[str, LanguageAdapter] = {
    "python": LanguageAdapter(
        name="python",
        extensions=(".py",),
        markers=("pyproject.toml", "requirements.txt", "setup.py"),
        # heal.py 를 돌리는 인터프리터를 그대로 씁니다.
        # 가상환경 안에서 실행했는데 시스템 python 의 pytest 를 찾는 사고를 막습니다.
        probe_cmd=[sys.executable, "--version"],
        test_cmd=[sys.executable, "-m", "pytest", "-q"],
        test_file_patterns=("test_*.py", "*_test.py", "conftest.py"),
        # 예: File "/path/to/util.py", line 12, in get
        trace_patterns=(r'File "([^"]+\.py)", line (\d+)',),
        # pytest -q 는 실제 실패 내역을 이 배너 뒤에 모아서 보여줍니다.
        failure_section_marker=r"=+\s*FAILURES\s*=+",
        # 파이썬 트레이스백은 원래 마지막 줄이 메시지라 이게 없어도 대체로 맞지만,
        # 명시적으로 둬서 "언어별로 정확한 줄을 고른다" 는 원칙을 지킵니다.
        error_summary_line_pattern=r"\b\w*(Error|Exception|Warning)\b",
        style_hint=(
            "타입 힌트를 유지하고, 방어 코드는 예외를 새로 던지는 대신 "
            "호출자가 다루기 쉬운 기본값이나 명시적 결과를 돌려주는 쪽을 택하십시오."
        ),
        compile_markers=("SyntaxError", "IndentationError"),
    ),
    "javascript": LanguageAdapter(
        name="javascript",
        extensions=(".js", ".mjs"),
        markers=("package.json",),
        probe_cmd=["node", "--version"],
        # node 18+ 내장 테스트 러너. package.json 에 scripts.test 가 있으면 그쪽이 우선입니다.
        test_cmd=["node", "--test"],
        test_file_patterns=("*.test.js", "*.test.mjs", "test_*.js", "*_test.js"),
        # 예: at get (/path/to/util.js:12:5)
        trace_patterns=(
            r"([A-Za-z]:[\\/][^\s:()]+\.m?js|/[^\s:()]+\.m?js):(\d+):\d+",
        ),
        # node --test(npm test 가 위임하는 기본 러너)는 실제 실패 목록을 이 배너
        # 뒤에 모아서 보여줍니다. 이게 없으면, 통과한 테스트가 (예: assert.throws
        # 로) 의도적으로 유발한 에러 스택까지 "실패 지점" 으로 잘못 읽힙니다.
        failure_section_marker=r"✖ failing tests:",
        # Node 는 에러 객체를 여러 줄짜리 속성 목록으로 찍고 그게 "}" 한 글자짜리
        # 줄로 끝나는 경우가 흔합니다. 그 줄을 그대로 요약으로 쓰면 의미가 없으니,
        # 실제 "Error: 메시지" 줄을 우선 찾습니다.
        error_summary_line_pattern=r"\b\w*(Error|Exception)\b",
        # node --test 기본 출력: "  ✔ 이름 (1.2ms)" / "  ✖ 이름 (1.2ms)".
        test_case_pattern=r"^\s*(✔|✖)\s+(.+?)\s+\([\d.]+m?s\)\s*$",
        style_hint="ESM 문법(import/export)을 유지하고 CommonJS 로 바꾸지 마십시오.",
        compile_markers=("SyntaxError", "Cannot find module"),
        symbol_patterns=(
            r"^\s*(?:export\s+)?(?:default\s+)?(?:async\s+)?function\s*\*?\s*(\w+)",
            r"^\s*(?:export\s+)?class\s+(\w+)",
            r"^\s*(?:export\s+)?(?:const|let|var)\s+(\w+)\s*=\s*(?:async\s*)?(?:function|\()",
        ),
    ),
    "go": LanguageAdapter(
        name="go",
        extensions=(".go",),
        markers=("go.mod",),
        probe_cmd=["go", "version"],
        test_cmd=["go", "test", "./..."],
        test_file_patterns=("*_test.go",),
        # 예: slice_util.go:12: index out of range
        trace_patterns=(r"([\w./\\-]+\.go):(\d+)",),
        # go 는 panic 메시지·소문자 error 관례를 씁니다(PascalCase Error 타입 이름이 아님).
        error_summary_line_pattern=r"(?i)panic:|\berror\b",
        style_hint="panic 을 새로 일으키지 말고 zero value 또는 error 반환으로 처리하십시오.",
        compile_markers=("[build failed]", "syntax error", "undefined:", "cannot use"),
        symbol_patterns=(
            # 메서드는 리시버 괄호를 건너뛰고 이름만 잡습니다.
            r"^func\s+(?:\([^)]*\)\s*)?(\w+)",
            r"^type\s+(\w+)",
        ),
    ),
}

# 의존성/빌드 산출물 디렉터리 — 우리 코드가 아니므로 절대 고치지 않습니다.
EXCLUDED_DIRS = {
    "node_modules", "vendor", "site-packages", ".venv", "venv",
    "__pycache__", ".git", "dist", "build", ".heal",
}


def extract_symbols(adapter: LanguageAdapter, source: str) -> set[str] | None:
    """소스의 최상위 심볼 목록을 뽑습니다. 판정할 수 없으면 ``None`` 입니다.

    **왜 필요한가** — 수정안은 파일 **전체**를 재생성합니다(``code_update_node``).
    그래서 LLM 이 고치라고 하지 않은 함수를 조용히 지워도, 테스트가 그 부분을 덮지
    않으면 그대로 통과합니다. G1~G8 이 전부 구조로 막는데 이 지점만 "프롬프트로
    부탁하기" 에 의존하고 있었습니다. 그 구멍을 메우는 것이 G9 입니다.

    파이썬은 ``ast`` 로 정확히 뽑고, **이름과 인자 수**를 함께 봅니다(``f/2``) —
    시그니처가 바뀌면 호출자가 깨지기 때문입니다. 다른 언어는 정규식이라 이름만 봅니다.

    파싱이 안 되면 ``None`` 을 돌려주고 **검증을 건너뜁니다.** 검증 장치가 수정 자체를
    막아서는 안 됩니다 (``MemoryStore.search`` 의 예외 처리와 같은 원칙).
    """
    if adapter.name == "python":
        try:
            tree = ast.parse(source)
        except SyntaxError:
            # LLM 이 문법을 깨뜨린 경우입니다. 그건 빌드 실패로 이미 잡힙니다.
            return None
        found: set[str] = set()
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                args = node.args
                count = (
                    len(args.posonlyargs) + len(args.args) + len(args.kwonlyargs)
                )
                found.add(f"{node.name}/{count}")
            elif isinstance(node, ast.ClassDef):
                found.add(node.name)
        return found

    if not adapter.symbol_patterns:
        return None
    names: set[str] = set()
    for pattern in adapter.symbol_patterns:
        names.update(m.group(1) for m in re.finditer(pattern, source, re.MULTILINE))
    return names


class LanguageDetectionError(RuntimeError):
    """언어를 특정하지 못했을 때 던집니다. exit code 3 (전제 실패) 으로 이어집니다."""


def iter_source_files(workdir: Path):
    """의존성 디렉터리를 뺀 파일만 훑습니다."""
    for path in workdir.rglob("*"):
        if not path.is_file():
            continue
        if any(part in EXCLUDED_DIRS for part in path.parts):
            continue
        yield path


def link_or_copy_dir(src: Path, dst: Path) -> str:
    """``dst`` 가 ``src`` 를 그대로 가리키게 링크를 겁니다. 안 되면 통째로 복사합니다.

    샌드박스에 ``node_modules`` 처럼 "읽기만 하고 절대 안 건드리는" 큰 디렉터리를
    준비할 때 씁니다. ``node_modules`` 는 ``EXCLUDED_DIRS`` 라 스캔·수정 대상에서도
    빠져 있어 통째로 복사할 이유가 없지만, 링크 없이 그냥 빼 버리면 ``npm test`` 가
    부트스트랩 단계에서 패키지를 못 찾아 죽습니다 — 그러면 실제 실패 원인과 무관하게
    거의 모든 테스트가 똑같이 뭉개진 오류로 실패해서 진단이 완전히 엉뚱한 방향으로 샙니다.

    돌려주는 문자열은 무엇으로 준비됐는지("junction"·"symlink"·"copy")입니다.
    """
    if sys.platform == "win32":
        # 심볼릭 링크는 관리자 권한이 필요할 수 있지만, NTFS 디렉터리 junction 은
        # 필요 없습니다.
        proc = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(dst), str(src)],
            # mklink 의 성공 메시지는 시스템 코드페이지(cp949 등)로 나옵니다.
            # UTF-8 로 강제 디코딩하면 콘솔 인코딩에 따라 깨질 수 있어 errors="replace" 로
            # 예외 없이 넘깁니다 — returncode 만 보므로 내용 자체는 중요하지 않습니다.
            capture_output=True, text=True, errors="replace",
        )
        if proc.returncode == 0:
            return "junction"
    else:
        try:
            os.symlink(src, dst, target_is_directory=True)
            return "symlink"
        except OSError:
            pass
    shutil.copytree(src, dst)
    return "copy"


def detect_language(workdir: Path) -> LanguageAdapter:
    """디렉터리 하나만 보고 언어를 결정합니다.

    사용자에게 받는 입력이 디렉터리 하나뿐이므로, 나머지는 전부 여기서 추론합니다.

    1순위는 마커 파일(go.mod, package.json ...)입니다. 확실하기 때문입니다.
    2순위는 소스 파일 확장자의 최빈값입니다. 마커가 없는 습작 디렉터리를 위한 보루입니다.
    둘 다 실패하면 추측하지 않고 에러를 냅니다. 여기서 LLM 에게 "알아서 해봐" 를 시키면
    존재하지 않는 테스트 명령을 지어내기 때문입니다.
    """
    for adapter in ADAPTERS.values():
        for marker in adapter.markers:
            if (workdir / marker).exists():
                return adapter

    counts: dict[str, int] = {}
    for path in iter_source_files(workdir):
        for adapter in ADAPTERS.values():
            if path.suffix in adapter.extensions:
                counts[adapter.name] = counts.get(adapter.name, 0) + 1

    if counts:
        return ADAPTERS[max(counts, key=lambda k: counts[k])]

    raise LanguageDetectionError(
        f"{workdir} 에서 언어를 특정하지 못했습니다. "
        f"지원 언어: {', '.join(ADAPTERS)}"
    )


def scan_workdir(adapter: LanguageAdapter, workdir: Path) -> tuple[int, int]:
    """한 번의 순회로 '고칠 수 있는 소스 개수' 와 '테스트 파일 개수' 를 함께 셉니다.

    4단계(스캔 집계)의 개수 표시와 3단계(테스트 존재 확인)가 예전에는 각자
    ``iter_source_files`` 를 따로 훑었습니다. 여기서 한 번에 묶어 순회를 줄입니다.

    테스트 개수는 화면에 "몇 개 파일을 고칠 수 있는가" 대신 "몇 개 테스트 파일을
    돌리는가" 를 보여주기 위한 것입니다 — 실제로 실행되는 건 소스 파일이 아니라
    테스트이므로, 사용자에게 의미 있는 숫자는 테스트 쪽입니다.
    """
    fixable = 0
    test_count = 0
    for path in iter_source_files(workdir):
        if adapter.is_test_file(path):
            test_count += 1
        elif adapter.is_source_file(path):
            fixable += 1
    return fixable, test_count


def has_tests(adapter: LanguageAdapter, workdir: Path) -> bool:
    """테스트 파일이 하나라도 있는지 봅니다.

    테스트는 이 시스템에서 **성공 판정 기준이자 명세**입니다.
    테스트가 없으면 "고쳤다" 를 판정할 방법이 없으므로, 추측으로 밀고 나가지 않고
    전제 실패(exit 3)로 끝냅니다. 스코프가 조용히 커지는 것을 막는 장치입니다.
    """
    return scan_workdir(adapter, workdir)[1] > 0


def find_project_root(start: Path) -> Path | None:
    """파일 하나에서 위로 올라가며 언어 마커를 찾아 프로젝트 루트를 정합니다.

    사용자가 파일을 지정했을 때만 쓰입니다. 테스트 통과가 유일한 성공 기준인데
    파일 하나에는 테스트도 언어 마커도 없으므로, 마커가 있는 곳까지 거슬러 올라가
    그 폴더를 ``workdir`` 로 삼습니다. 어떤 언어인지 아직 모르는 시점이라
    ``ADAPTERS`` 전체의 마커를 다 확인합니다.
    """
    current = start if start.is_dir() else start.parent
    for candidate in (current, *current.parents):
        for adapter in ADAPTERS.values():
            if any((candidate / marker).exists() for marker in adapter.markers):
                return candidate
    return None


def find_git_root(start: Path) -> Path | None:
    """``start`` 에서 위로 올라가며 ``.git`` 이 있는 폴더(리포지토리 루트)를 찾습니다.

    모노레포에서는 테스트가 ``workdir`` 밖, 리포지토리 루트에 있는 파일(예:
    ``.github/workflows/*.yml``)을 상대 경로로 읽는 경우가 있습니다. 샌드박스가
    ``workdir`` 만 복사하면 그런 파일이 사본에 없어서 ``ENOENT`` 가 납니다 — 실제
    코드 결함이 아니라 샌드박스 구조 문제입니다. 이 함수는 그런 경우를 감지하는
    용도로, ``.git`` 위치를 찾아 리포지토리 루트를 돌려줍니다."""
    current = start.resolve() if start.is_dir() else start.resolve().parent
    for candidate in (current, *current.parents):
        if (candidate / ".git").exists():
            return candidate
    return None


def is_git_dirty(path: Path, git_root: Path) -> bool | None:
    """``path`` 가 ``git_root`` 리포지토리에서 커밋되지 않은 변경을 갖고 있는지 봅니다.

    수정 대상을 "커밋 안 된 파일" 로만 제한하는 가드레일의 판단 근거입니다.
    이미 커밋된 안정 코드를 자동으로 고치기 시작하면, 실패한 테스트와 무관한
    범위까지 손대게 될 위험이 있어 작업 중인(dirty) 파일로만 범위를 좁힙니다.

    git 명령이 실패하거나(git 미설치·리포지토리 아님) 파일이 리포지토리 밖이면
    판단할 수 없으므로 ``None`` 을 돌려줍니다 — 이 경우 가드레일은 판단을
    보류하고 수정을 막지 않습니다. git 이 없는 환경에서 이 안전장치 때문에
    핵심 기능 전체가 막히면 안 되기 때문입니다.
    """
    try:
        rel = path.resolve().relative_to(git_root.resolve())
    except (ValueError, OSError):
        return None
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain", "--", str(rel)],
            cwd=git_root,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    return bool(result.stdout.strip())


def count_dirty_files(scope_dir: Path, git_root: Path) -> int | None:
    """``scope_dir`` 안에서 커밋되지 않은 변경을 가진 파일 수를 셉니다.

    ``is_git_dirty`` 와 같은 판단 근거(G12)를 실행 전에 화면에 요약해서, "이번
    실행이 실제로 몇 개 파일을 후보로 볼 수 있는지" 를 미리 보여주기 위한
    용도입니다. git 명령이 실패하면(git 미설치·리포지토리 아님) 판단할 수
    없으므로 ``None`` 을 돌려줍니다.
    """
    try:
        rel = scope_dir.resolve().relative_to(git_root.resolve())
    except (ValueError, OSError):
        return None
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain", "--", str(rel) if str(rel) != "." else "."],
            cwd=git_root,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    return sum(1 for line in result.stdout.splitlines() if line.strip())


def resolve_test_cmd(adapter: LanguageAdapter, workdir: Path) -> list[str]:
    """프로젝트가 자기 테스트 명령을 정의해 두었으면 그쪽을 존중합니다.

    지금은 package.json 의 ``scripts.test`` 만 봅니다. 모노레포처럼 관례를 벗어난
    경우에는 CLI 의 ``--test-cmd`` 로 덮어쓸 수 있습니다.
    """
    if adapter.name == "javascript":
        pkg = workdir / "package.json"
        if pkg.exists():
            try:
                data = json.loads(pkg.read_text(encoding="utf-8"))
                script = (data.get("scripts") or {}).get("test")
                # npm 기본 스텁("no test specified")은 테스트가 아니므로 무시합니다.
                if script and "no test specified" not in script:
                    # Windows 에서 npm 은 .exe 가 아니라 npm.cmd 배치 파일입니다.
                    # subprocess 가 shell=False 로 실행할 때 "npm" 만 주면 CreateProcess
                    # 가 그 이름 그대로 찾다가 [WinError 2] 로 실패합니다 — 확장자를
                    # 명시해야 배치 파일도 직접 실행됩니다.
                    npm = "npm.cmd" if sys.platform == "win32" else "npm"
                    return [npm, "test", "--silent"]
            except (json.JSONDecodeError, OSError):
                pass
    return list(adapter.test_cmd)


def preflight(adapter: LanguageAdapter) -> tuple[bool, str]:
    """툴체인이 실제로 깔려 있는지 그래프 진입 **전에** 확인합니다.

    이게 없으면 최악의 시나리오가 생깁니다. go 가 안 깔린 머신에서 테스트는 무조건
    실패하고, 에이전트는 멀쩡한 코드를 계속 고치려 들면서 토큰만 태웁니다.
    데모가 조용히 망가지는 전형적인 경로라 반드시 먼저 막습니다.
    """
    exe = adapter.probe_cmd[0]
    if shutil.which(exe) is None:
        return False, f"'{exe}' 실행 파일을 찾지 못했습니다. {adapter.name} 툴체인을 설치하십시오."
    try:
        proc = subprocess.run(adapter.probe_cmd, capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, f"툴체인 확인 실패: {exc}"
    if proc.returncode != 0:
        return False, f"툴체인 확인 실패: {' '.join(adapter.probe_cmd)} → exit {proc.returncode}"
    first_line = (proc.stdout or proc.stderr).strip().splitlines()
    return True, first_line[0] if first_line else exe


def _failure_section(adapter: LanguageAdapter, output: str) -> str:
    """``adapter.failure_section_marker`` 뒤쪽만 잘라냅니다.

    ``locate_targets`` 와 ``LanguageAdapter.is_build_failure`` 가 공통으로 씁니다 —
    둘 다 "전체 출력" 이 아니라 "이번에 실제로 터진 구간" 만 봐야 정확하다는 같은
    이유 때문입니다. 마커를 못 찾거나 애초에 정의돼 있지 않으면 원본 그대로 돌려줍니다.
    """
    if adapter.failure_section_marker:
        marker_match = re.search(adapter.failure_section_marker, output)
        if marker_match:
            return output[marker_match.start():]
    return output


def extract_test_cases(adapter: LanguageAdapter, output: str) -> list[dict] | None:
    """개별 테스트 결과 전부를 뽑습니다. ``[{"name": str, "passed": bool}, ...]``.

    "테스트 파일 N개" 는 몇 개를 돌리는지일 뿐, 실제로 몇 개의 개별 테스트가
    통과/실패했는지는 이 함수로만 알 수 있습니다. ``adapter.test_case_pattern`` 이
    없으면(아직 python·go 는 기본 출력에 개별 이름이 안 찍혀서 지원하지 않습니다)
    ``None`` 을 돌려줍니다 — 호출자는 예전처럼 파일 단위 통과/실패만 보여줍니다.
    """
    if not adapter.test_case_pattern:
        return None
    cases = []
    for match in re.finditer(adapter.test_case_pattern, output, re.MULTILINE):
        symbol, name = match.group(1), match.group(2).strip()
        cases.append({"name": name, "passed": symbol == "✔"})
    return cases or None


def locate_targets(
    adapter: LanguageAdapter, output: str, workdir: Path
) -> list[tuple[Path, int]]:
    """테스트 실패 출력에서 '고쳐야 할 파일' 을 **전부** 뽑습니다.

    사용자가 파일 경로를 주지 않으므로, 이 함수가 그 자리를 대신합니다.
    핵심 아이디어는 단순합니다. **실패 출력에는 이미 답이 들어 있습니다.**

        --- FAIL: TestGet (0.00s)
            slice_util.go:12: index out of range [5] with length 3

    규칙 기반으로 먼저 시도하는 이유는 빠르고 공짜이기 때문입니다.
    (규칙이 실패하면 호출자가 LLM 폴백을 씁니다.)
    **LLM 을 쓰지 않으므로 착수 확인 게이트보다 앞에 둘 수 있습니다.**
    사용자에게 "대상 몇 개, 예상 몇 초" 를 돈이 나가기 전에 보여 주는 근거가 여기서 나옵니다.

    필터가 중요합니다. 스택트레이스에는 표준 라이브러리와 테스트 러너 프레임이 섞여
    들어오는데, 그것들을 고치려 들면 안 됩니다. 그래서
    **workdir 안 + 소스 파일 + 테스트 파일 아님** 세 조건을 모두 만족하는 것만 남깁니다.
    (차례로 G2 · G3 · G1 을 이 자리에서 강제하는 것입니다.)

    같은 파일이 스택트레이스에 여러 번 나오는 것은 흔한 일이므로 **파일당 한 건으로**
    줄입니다. 줄 번호는 마지막에 관측된 것을 남깁니다. 프레임 순서가 바깥 → 안쪽이라
    마지막이 실제 터진 지점에 가장 가깝기 때문입니다.

    돌려주는 순서도 같은 이유로 **안쪽 프레임 우선**입니다. 그래서 이 목록의 첫 번째가
    단일 대상을 고를 때의 답과 같습니다 (``locate_target`` 이 그렇게 쓰고 있습니다).
    """
    # 통과한 테스트가 부산물로 남긴 스택트레이스를 실패 지점으로 착각하지 않도록,
    # 이번에 실제로 터진 구간만 봅니다.
    output = _failure_section(adapter, output)

    root = workdir.resolve()
    # 경로 → (줄번호, 출력에서 마지막으로 등장한 위치)
    found: dict[Path, tuple[int, int]] = {}

    for pattern in adapter.trace_patterns:
        for match in re.finditer(pattern, output):
            raw, line_no = match.group(1), int(match.group(2))
            path = Path(raw)
            if not path.is_absolute():
                path = workdir / path
            try:
                path = path.resolve()
                path.relative_to(root)  # workdir 밖이면 ValueError (G2)
            except (ValueError, OSError):
                continue
            if any(part in EXCLUDED_DIRS for part in path.parts):  # G3
                continue
            if not path.exists() or not adapter.is_source_file(path):  # G1 + 확장자 화이트리스트
                continue
            # 나중 등장이 이기게 둡니다. 마지막이 실제 터진 지점에 가장 가깝습니다.
            found[path] = (line_no, match.start())

    # 마지막 등장이 늦은 것 = 안쪽 프레임 → 먼저 고칩니다.
    ordered = sorted(found.items(), key=lambda kv: kv[1][1], reverse=True)
    return [(path, line_no) for path, (line_no, _) in ordered]


def locate_test_files(
    adapter: LanguageAdapter, output: str, workdir: Path
) -> list[Path]:
    """실패 출력에서 **테스트 파일**(고칠 대상이 아니라 명세) 을 찾습니다.

    ``locate_targets`` 와 거의 같은 필터(G2·G3)를 쓰지만, 반대로 ``is_test_file`` 만
    남깁니다. exit 1(못 고침) 뒤에 남기는 참고용 알림(``notice.html``)에 "무엇을
    통과시켜야 하는가"를 같이 보여주기 위한 용도입니다 — 수정 대상 선정과는 무관하고,
    이 파일 목록은 절대 수정하지 않습니다.
    """
    output = _failure_section(adapter, output)
    root = workdir.resolve()
    found: dict[Path, int] = {}

    for pattern in adapter.trace_patterns:
        for match in re.finditer(pattern, output):
            raw = match.group(1)
            path = Path(raw)
            if not path.is_absolute():
                path = workdir / path
            try:
                path = path.resolve()
                path.relative_to(root)
            except (ValueError, OSError):
                continue
            if any(part in EXCLUDED_DIRS for part in path.parts):
                continue
            if not path.exists() or not adapter.is_test_file(path):
                continue
            found[path] = match.start()

    ordered = sorted(found.items(), key=lambda kv: kv[1], reverse=True)
    return [path for path, _ in ordered]


def locate_target(
    adapter: LanguageAdapter, output: str, workdir: Path
) -> tuple[Path | None, int | None]:
    """수정 대상 **하나**만 필요할 때 쓰는 편의 함수입니다.

    ``locate_targets`` 의 첫 번째 결과, 즉 가장 안쪽 프레임을 돌려줍니다.
    """
    targets = locate_targets(adapter, output, workdir)
    if not targets:
        return None, None
    return targets[0]
