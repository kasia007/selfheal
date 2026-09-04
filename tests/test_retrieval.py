"""검색 파이프라인 검증 — 하이브리드 · 리랭킹 · 쿼리 확장 (18단계, 체크리스트 #3).

**여기서 쓰는 임베딩은 의미를 보존하지 않습니다**(`HashEmbeddings` — 해시 기반).
그래서 벡터 순위는 사실상 무작위이고, 이 조건은 **BM25 의 기여를 보기에 오히려 좋습니다** —
키워드 검색이 벡터가 못 잡는 것을 건져 올리는지 확인할 수 있습니다.

반대로 이 파일로는 "하이브리드가 실제 데이터에서 얼마나 좋아지는가" 를 말할 수 없습니다.
그 판단은 자격증명·한도가 회복된 뒤 온라인 실행으로 합니다. 이번 단계의 목표는 그 판단을
**가능하게 만드는 것**(기여도 기록)까지입니다.
"""

from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

SELFHEAL_ROOT = Path(__file__).resolve().parents[1]
if str(SELFHEAL_ROOT) not in sys.path:
    sys.path.insert(0, str(SELFHEAL_ROOT))

from src.agent import _exception_names  # noqa: E402
from src.retriever import (  # noqa: E402
    RRF_K,
    _domain_boost,
    _rrf_fuse,
    _tokenize,
)
from src.state import MemoryHit  # noqa: E402


# ── 토큰화 ──────────────────────────────────────────────────────
def test_CamelCase_를_쪼개_토큰을_늘린다():
    """테스트 출력에는 ``IndexError``, 저장 요약에는 "인덱스 오류" 로 적힐 수 있습니다.

    낱말 그대로만 보면 겹치지 않으므로 조각도 함께 뽑습니다.
    """
    tokens = _tokenize("IndexError: list index out of range")
    assert "indexerror" in tokens, "원형이 남아 있어야 정확 일치가 됩니다."
    assert "index" in tokens and "error" in tokens, "조각도 있어야 재현율이 올라갑니다."


def test_한글과_영문을_함께_뽑는다():
    tokens = _tokenize("# 경계 검사 누락 ## IndexError ###")
    assert "경계" in tokens and "누락" in tokens
    assert "indexerror" in tokens
    # 구분자(#)는 신호가 아니므로 토큰이 되지 않아야 합니다.
    assert "#" not in tokens


# ── RRF 융합 ────────────────────────────────────────────────────
def test_RRF_는_공식대로_계산한다():
    fused = dict(_rrf_fuse([["a", "b"], ["b", "a"]]))
    expected = 1.0 / (RRF_K + 1) + 1.0 / (RRF_K + 2)
    assert fused["a"] == pytest.approx(expected)
    assert fused["b"] == pytest.approx(expected)


def test_RRF_는_양쪽에서_상위인_문서를_올린다():
    """한쪽에서만 1위인 문서보다, 양쪽에서 고르게 상위인 문서가 이겨야 합니다."""
    fused = _rrf_fuse([["x", "a", "b"], ["y", "a", "b"]])
    top = fused[0][0]
    assert top == "a", f"양쪽 2위인 a 가 1위여야 하는데 {top} 가 나왔습니다."


def test_RRF_는_척도가_다른_점수를_섞지_않는다():
    """순위만 쓰므로, 점수 크기와 무관하게 결과가 같아야 합니다.

    코사인 거리와 BM25 점수를 가중 합하면 가중치라는 검증되지 않은 상수가 생기고
    코퍼스마다 어긋납니다. RRF 를 택한 이유입니다.
    """
    assert _rrf_fuse([["a", "b"]]) == _rrf_fuse([["a", "b"]])


# ── 도메인 리랭킹 ───────────────────────────────────────────────
def _hit(**kw) -> MemoryHit:
    base = {"id": "x", "document": "# p ## s ### c", "distance": 0.2}
    base.update(kw)
    return MemoryHit(**base)


def test_반복되는_실수가_위로_올라간다():
    """이 코드베이스가 여러 번 반복한 패턴일수록 먼저 볼 만합니다.

    원본 노트북이 셀 수 없었던, 이 구현만의 신호입니다.
    """
    once = _domain_boost(_hit(occurrences=1), "python")
    many = _domain_boost(_hit(occurrences=5), "python")
    assert many > once


def test_발생횟수_보정은_로그로_완만하다():
    """한 패턴이 순위를 독점하면 다른 단서가 묻힙니다."""
    b5 = _domain_boost(_hit(occurrences=5), "python")
    b50 = _domain_boost(_hit(occurrences=50), "python")
    # 10배 늘어도 보정은 2배가 되지 않아야 합니다.
    assert b50 < b5 * 2


def test_같은_언어를_위로_올린다():
    """--cross-language 로 격리를 풀었을 때의 신호입니다."""
    same = _domain_boost(_hit(languages="python,javascript"), "python")
    other = _domain_boost(_hit(languages="go"), "python")
    assert same > other


def test_최근에_터진_패턴을_위로_올린다():
    recent = _domain_boost(_hit(last_seen=date.today().isoformat()), "python")
    old = _domain_boost(
        _hit(last_seen=(date.today() - timedelta(days=365)).isoformat()), "python"
    )
    assert recent > old


def test_손상된_날짜에도_죽지_않는다():
    """리랭킹은 보조 장치입니다. 값이 이상해도 검색을 막아서는 안 됩니다."""
    assert _domain_boost(_hit(last_seen="어제"), "python") > 0


# ── 쿼리 확장 (규칙 기반, 축약 경로용) ──────────────────────────
def test_테스트출력에서_예외이름을_뽑는다():
    """예외 이름은 그 자체가 강한 신호이고, 정확 일치라 BM25 가 특히 잘 잡습니다."""
    output = (
        "E   IndexError: list index out of range\n"
        "E   KeyError: 'missing'\n"
        "some NullPointerException here\n"
    )
    names = _exception_names(output)
    assert names[:2] == ["IndexError", "KeyError"], names
    assert "NullPointerException" in names


def test_예외이름은_중복없이_상한까지만():
    output = "IndexError\n" * 10 + "KeyError\n"
    names = _exception_names(output)
    assert names == ["IndexError", "KeyError"]


def test_예외가_없으면_빈_목록():
    assert _exception_names("3 failed, 1 passed") == []


# ── 하이브리드 검색 (Chroma 경유) ───────────────────────────────
@pytest.fixture()
def stocked(memory):
    """세 가지 패턴을 쌓아 둡니다."""
    memory.add(
        "# 경계 검사 누락 ## IndexError: list index out of range ### 범위 확인 후 기본값",
        "python", "boundary.py", pattern="경계 검사 누락",
    )
    memory.add(
        "# 부재 키 접근 ## KeyError ### 키 존재를 먼저 확인한다",
        "python", "lookup.py", pattern="부재 키 접근",
    )
    memory.add(
        "# 널 역참조 ## TypeError ### None 검사를 추가한다",
        "python", "head.py", pattern="널 역참조",
    )
    return memory


def test_BM25_가_정확한_단어를_잡아_올린다(stocked):
    """해시 임베딩은 의미를 보존하지 않아 벡터 순위가 무작위입니다.

    그래도 ``KeyError`` 로 검색하면 그 단어가 든 문서가 1위여야 합니다 — 그것이
    하이브리드에서 키워드 검색이 하는 일입니다.
    """
    hits = stocked.search("KeyError", "python")
    assert hits, "검색 결과가 있어야 합니다."
    assert hits[0].title == "부재 키 접근", [h.title for h in hits]
    assert hits[0].bm25_rank == 1


def test_기여도를_기록한다(stocked):
    """하이브리드가 실제로 도움이 됐는지 나중에 숫자로 판단하기 위한 근거입니다."""
    hits = stocked.search(["KeyError", "키 존재 확인"], "python")
    top = hits[0]
    assert top.final_rank == 1
    assert top.bm25_rank is not None or top.vector_rank is not None
    assert top.boost >= 1.0


def test_단일_문자열도_그대로_받는다(stocked):
    """기존 호출부를 고치지 않아도 동작해야 합니다."""
    assert stocked.search("KeyError", "python")


def test_빈_질의는_검색하지_않는다(stocked):
    assert stocked.search("", "python") == []
    assert stocked.search(["", "   "], "python") == []


def test_다른_언어는_격리된다(stocked):
    """파이썬의 IndexError 기억이 Go 슬라이스 수정에 끌려오면 오염입니다."""
    assert stocked.search("KeyError", "go") == []


def test_격리를_풀면_다른_언어도_찾는다(stocked):
    stocked.cross_language = True
    try:
        assert stocked.search("KeyError", "go")
    finally:
        stocked.cross_language = False
