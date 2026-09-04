"""RAGAS 평가 — 메모리 검색과 패치 생성을 정량으로 채점합니다.

이 프로젝트는 문서 질의응답 RAG 가 아니지만, **검색-증강 생성의 구조는 동일합니다.**
그래서 RAGAS 지표를 다음과 같이 대응시킵니다.

    user_input          ← 에이전트가 작성한 버그 리포트
    retrieved_contexts  ← 메모리에서 검색해 수정 프롬프트에 주입한 과거 버그 패턴
    response            ← 실제로 적용된 수정 요약 (patch diff 기반)
    reference           ← 사람이 정의한 정답 수정 전략 (eval_set.json)

지표는 두 층으로 봅니다.

1. **RAGAS** — 검색이 옳았는가(context precision/recall), 생성이 근거에 충실했는가
   (faithfulness), 질문에 답했는가(response relevancy).
2. **도메인 지표** — 이 프로젝트에서 진짜 중요한 값입니다.
   테스트 통과 여부(pass@1)와 **재시도 횟수**. RAGAS 점수가 좋아도 테스트가 안 통과하면
   실패입니다. 반대로 재시도 횟수 감소가 메모리 효과의 직접 증거입니다.

실행 (먼저 run_inout.py 를 돌려 리포트 골격을 만들어 둡니다):
    python evaluation/run_inout.py --online            # report.md 골격
    python evaluation/run_ragas.py                      # report.md 에 RAGAS 절 추가

    python evaluation/run_inout.py --online --round 2   # round2_report.md 골격
    python evaluation/run_ragas.py --round 2            # round2_report.md 에 RAGAS 절 추가

``--round`` 는 어느 스냅샷 파일에 이어 쓸지 정하는 선택 사항입니다(§4-1 규약의
round1/round2 는 메모리 on/off 대조가 아니라 **개발 진행 중 두 시점**을 나눠 남기라는
뜻입니다 — round1_report.md 참고).

AWS 자격증명이 필요합니다. 결과는 evaluation/report.md(또는 round{N}_report.md) 와
evaluation/ragas.json 에 기록됩니다.
"""

from __future__ import annotations

import argparse
import json
import shutil
import statistics
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src import agent as heal  # noqa: E402  (.env 로더 재사용)
from src.agent import HealingNodes, build_graph  # noqa: E402
from src.report import Console, make_diff  # noqa: E402
from src.retriever import MemoryStore  # noqa: E402
from src.state import State  # noqa: E402
from src.tools import detect_language, preflight, resolve_test_cmd  # noqa: E402

EVAL_DIR = Path(__file__).resolve().parent
EVAL_SET = EVAL_DIR / "eval_set.json"


def run_case(case: dict, memory: MemoryStore, max_attempts: int) -> dict:
    """케이스 하나를 **임시 복사본**에서 돌립니다.

    샘플 원본을 고쳐 버리면 두 번째 실행부터 '이미 통과하는 코드' 가 되어
    평가가 성립하지 않습니다. 그래서 매번 깨끗한 사본을 씁니다.
    """
    src = ROOT / case["workdir"]
    with tempfile.TemporaryDirectory() as tmp:
        workdir = Path(tmp) / src.name
        shutil.copytree(src, workdir)

        adapter = detect_language(workdir)
        ok, info = preflight(adapter)
        if not ok:
            return {"id": case["id"], "skipped": info}

        console = Console(quiet=True)
        nodes = HealingNodes(adapter=adapter, memory=memory, console=console)
        graph = build_graph(nodes)

        started = time.time()
        result = graph.invoke(
            State(
                workdir=str(workdir),
                language=adapter.name,
                test_cmd=resolve_test_cmd(adapter, workdir),
                max_attempts=max_attempts,
            ),
            {"recursion_limit": max_attempts * 15 + 20},
        )
        state = State.model_validate(result)

        diff = ""
        if state.target_file and state.original_source:
            final = Path(state.target_file).read_text(encoding="utf-8")
            diff = make_diff(state.original_source, final, Path(state.target_file).name)

        return {
            "id": case["id"],
            "role": case.get("role", "eval"),
            "family": case.get("family", ""),
            "language": adapter.name,
            "status": state.status,
            "fixed": state.status == "fixed",
            "attempts": state.attempts,
            "duration_sec": round(time.time() - started, 1),
            "memory_hit_count": len(state.memory_hits),
            "injected_count": len(state.injected_hits()),
            # ── RAGAS 입력 ──
            "user_input": state.bug_report,
            "retrieved_contexts": [h.document for h in state.injected_hits()],
            "response": diff or "(수정 없음)",
            "reference": case["reference"],
        }


def score_with_ragas(rows: list[dict]) -> dict:
    """RAGAS 채점. ragas 나 자격증명이 없으면 사유를 담아 돌려줍니다.

    숫자를 지어내지 않는 것이 중요합니다. 미실행은 미실행이라고 적습니다.
    """
    usable = [r for r in rows if r.get("retrieved_contexts")]
    if not usable:
        return {
            "status": "skipped",
            "reason": (
                "주입된 메모리가 없어 검색 지표를 계산할 수 없습니다. "
                "warmup 케이스로 메모리를 먼저 채웠는지, --merge-threshold 가 너무 "
                "빡빡하지 않은지 확인하십시오."
            ),
        }

    try:
        from datasets import Dataset
        from langchain_aws import BedrockEmbeddings, ChatBedrock
        from ragas import evaluate
        from ragas.embeddings import LangchainEmbeddingsWrapper
        from ragas.llms import LangchainLLMWrapper
        from ragas.metrics import (
            Faithfulness,
            LLMContextPrecisionWithReference,
            LLMContextRecall,
            ResponseRelevancy,
        )
    except ImportError as exc:
        return {
            "status": "skipped",
            "reason": f"의존성 없음: {exc}. `pip install -r requirements.txt` 후 다시 실행하십시오.",
        }

    from src.agent import LLM_MODEL_ID
    from src.retriever import BEDROCK_REGION, EMBED_MODEL_ID

    judge = LangchainLLMWrapper(
        ChatBedrock(
            model_id=LLM_MODEL_ID,
            region_name=BEDROCK_REGION,
            model_kwargs={"temperature": 0},
        )
    )
    embedder = LangchainEmbeddingsWrapper(
        BedrockEmbeddings(model_id=EMBED_MODEL_ID, region_name=BEDROCK_REGION)
    )

    dataset = Dataset.from_list(
        [
            {
                "user_input": r["user_input"],
                "retrieved_contexts": r["retrieved_contexts"],
                "response": r["response"],
                "reference": r["reference"],
            }
            for r in usable
        ]
    )

    try:
        result = evaluate(
            dataset,
            metrics=[
                LLMContextPrecisionWithReference(),
                LLMContextRecall(),
                Faithfulness(),
                ResponseRelevancy(),
            ],
            llm=judge,
            embeddings=embedder,
        )
    except Exception as exc:  # 자격증명·쿼터 등 실행 시점 문제
        return {"status": "failed", "reason": f"{type(exc).__name__}: {exc}"}

    scores = {k: round(float(v), 3) for k, v in result._repr_dict.items()}
    return {"status": "ok", "n": len(usable), "scores": scores}


def render_report(payload: dict) -> str:
    """제출용 마크다운 리포트를 만듭니다."""
    rows = payload["cases"]
    ragas = payload["ragas"]
    lines: list[str] = []

    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("# RAGAS 평가 (검색·생성 품질)")
    lines.append("")
    lines.append(f"- 실행 시각: {payload['timestamp']}")
    lines.append("- 메모리: 항상 ON")
    lines.append(f"- 최대 재시도: {payload['max_attempts']}회")
    lines.append("")

    lines.append("## 1. 도메인 지표 (실제로 고쳤는가)")
    lines.append("")
    lines.append("RAGAS 점수가 아무리 좋아도 테스트가 통과하지 않으면 실패입니다.")
    lines.append("이 표가 최종 판정이고, RAGAS 는 그 과정의 품질을 설명하는 보조 지표입니다.")
    lines.append("")
    lines.append("| 케이스 | 언어 | 결과 | 시도 | 검색 | 주입 | 소요(초) |")
    lines.append("|---|---|---|---|---|---|---|")
    for r in rows:
        if r.get("skipped"):
            lines.append(f"| {r['id']} | - | 건너뜀 ({r['skipped']}) | - | - | - | - |")
            continue
        mark = "✅ fixed" if r["fixed"] else f"❌ {r['status']}"
        lines.append(
            f"| {r['id']} | {r['language']} | {mark} | {r['attempts']} | "
            f"{r['memory_hit_count']} | {r['injected_count']} | {r['duration_sec']} |"
        )
    lines.append("")

    graded = [r for r in rows if not r.get("skipped")]
    if graded:
        fixed = [r for r in graded if r["fixed"]]
        lines.append(f"- 수정 성공률: **{len(fixed)}/{len(graded)}**")
        if fixed:
            mean_attempts = statistics.mean(r["attempts"] for r in fixed)
            lines.append(f"- 성공 케이스 평균 시도 횟수: **{mean_attempts:.2f}회**")
            lines.append(
                "  - **메모리 효과는 이 값과 주입 건수(`injected`) 를 함께 봐야 합니다.** "
                "주입이 0건인데 시도가 적다면 메모리와 무관하게 쉬운 버그였다는 뜻입니다."
            )
    lines.append("")

    lines.append("## 2. RAGAS 지표 (검색과 생성의 품질)")
    lines.append("")
    if ragas["status"] == "ok":
        lines.append(f"평가 대상: 메모리가 실제로 주입된 {ragas['n']}건")
        lines.append("")
        lines.append("| 지표 | 점수 | 이 프로젝트에서의 의미 |")
        lines.append("|---|---|---|")
        meaning = {
            "llm_context_precision_with_reference": "검색된 과거 패턴이 이번 버그와 실제로 관련 있었는가",
            "context_recall": "정답 수정 전략에 필요한 정보가 검색 결과에 들어 있었는가",
            "faithfulness": "생성된 패치가 주입된 과거 사례에 근거했는가 (지어내지 않았는가)",
            "answer_relevancy": "패치가 버그 리포트가 지적한 문제에 실제로 답했는가",
        }
        for key, value in ragas["scores"].items():
            lines.append(f"| `{key}` | **{value}** | {meaning.get(key, '')} |")
    else:
        lines.append(f"> ⚠️ **미실행 ({ragas['status']})** — {ragas['reason']}")
        lines.append(">")
        lines.append("> 수치를 임의로 채우지 않았습니다. 아래 명령으로 다시 실행하면 이 절이 채워집니다.")
        lines.append(">")
        lines.append("> ```bash")
        lines.append("> pip install -r requirements.txt")
        lines.append("> export AWS_ACCESS_KEY_ID=... AWS_SECRET_ACCESS_KEY=... AWS_DEFAULT_REGION=us-east-1")
        lines.append("> python evaluation/run_ragas.py")
        lines.append("> ```")
    lines.append("")

    lines.append("## 3. 해석 시 주의")
    lines.append("")
    lines.append("- **처음 실행에는 메모리가 비어 있어 주입이 일어나지 않습니다.** "
                 "`warmup` 케이스로 패턴을 먼저 쌓은 뒤 `eval` 케이스를 채점하는 이유입니다.")
    lines.append("- **같은 버그를 다시 돌리는 것은 캐시일 뿐 학습이 아닙니다.** "
                 "eval 케이스는 warmup 과 계열만 같고 형태가 다른 버그로 구성했습니다.")
    lines.append("- `--merge-threshold` 는 실험 파라미터입니다. 너무 빡빡하면 같은 버그가 "
                 "별개 패턴으로 쪼개지고, 너무 느슨하면 무관한 버그가 뭉쳐 발생 횟수가 부풀려집니다.")
    lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="selfheal RAGAS 평가")
    parser.add_argument("--max-attempts", type=int, default=3)
    parser.add_argument(
        "--fresh-memory",
        action="store_true",
        help="평가 전용 임시 메모리를 씁니다. 누적 메모리를 오염시키지 않습니다.",
    )
    parser.add_argument(
        "--round", type=int, choices=[1, 2],
        help="round{N}_report.md 뒤에 RAGAS 절을 덧붙입니다. 생략하면 report.md.",
    )
    heal.load_dotenv_if_present()
    args = parser.parse_args(argv)

    spec = json.loads(EVAL_SET.read_text(encoding="utf-8"))
    memory_dir = (
        Path(tempfile.mkdtemp(prefix="selfheal-eval-")) if args.fresh_memory
        else ROOT / "chroma_db"
    )
    memory = MemoryStore(memory_dir)

    rows: list[dict] = []
    # RAGAS 는 실제 수정이 일어나는 fix 케이스만 채점합니다.
    # 가드레일·전제 케이스는 검색/생성이 없으므로 지표가 정의되지 않습니다.
    fix_cases = [c for c in spec["cases"] if c.get("type", "fix") == "fix"]
    for case in fix_cases:
        print(f"▶ {case['id']} ({case.get('role', 'eval')}) ...", flush=True)
        try:
            row = run_case(case, memory, args.max_attempts)
        except Exception as exc:
            # 자격증명이 없으면 여기서 걸립니다. 리포트는 그래도 남겨야
            # '무엇이 왜 미실행인지' 가 산출물에 드러납니다.
            row = {"id": case["id"], "skipped": f"{type(exc).__name__}: {exc}"}
        rows.append(row)
        if row.get("skipped"):
            print(f"  건너뜀: {row['skipped']}")
        else:
            print(f"  {row['status']} / 시도 {row['attempts']}회 / 주입 {row['injected_count']}건")

    # 채점은 eval 역할만 합니다. warmup 은 메모리를 채우는 용도입니다.
    graded = [r for r in rows if r.get("role") == "eval" and not r.get("skipped")]
    if not graded:
        # 케이스가 하나도 돌지 못한 경우(대개 자격증명 없음)를 검색 실패로 오인하지 않게
        # 사유를 그대로 전달합니다.
        reasons = {r.get("skipped", "알 수 없음") for r in rows}
        ragas = {
            "status": "skipped",
            "reason": "채점할 fix 케이스가 실행되지 않았습니다: " + "; ".join(sorted(reasons)),
        }
    else:
        ragas = score_with_ragas(graded)

    payload = {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "max_attempts": args.max_attempts,
        "cases": rows,
        "ragas": ragas,
    }

    json_name = f"round{args.round}_ragas.json" if args.round else "ragas.json"
    (EVAL_DIR / json_name).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    # run_inout.py 가 먼저 써 둔 인-아웃 결과 뒤에 RAGAS 절을 덧붙입니다.
    report_path = EVAL_DIR / (f"round{args.round}_report.md" if args.round else "report.md")
    with report_path.open("a", encoding="utf-8") as f:
        f.write(render_report(payload))
    print(f"\n리포트: {report_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
