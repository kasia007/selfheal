"""버그 패턴 메모리 — Chroma + Bedrock Titan 임베딩.

원본 노트북 대비 이 파일이 바꾸는 것이 세 가지입니다.

1. **``PersistentClient``**
   원본은 ``chromadb.Client()`` 라 프로세스가 끝나면 기억이 전부 날아갑니다.
   그러면 "학습했다" 를 회차 간에 비교할 수가 없습니다.

2. **발생 횟수 카운터**
   원본은 유사 버그를 찾으면 문서를 덮어쓰기만 해서, 같은 패턴이 몇 번 터졌는지
   어디에도 남지 않습니다. metadata 에 ``occurrences`` 를 두고 병합할 때마다 올립니다.

3. **언어별 격리**
   metadata 에 ``language`` 를 붙이고 기본적으로 같은 언어 안에서만 찾습니다.
   그러지 않으면 파이썬의 IndexError 기억이 Go 슬라이스 수정에 끌려와 오염됩니다.
   (``cross_language=True`` 로 격리를 풀면 "언어를 넘는 패턴 전이" 실험이 됩니다.)

임베딩은 이 저장소 지침(AGENTS.md)에 따라 Bedrock Titan 을 씁니다.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import uuid
from datetime import date, datetime
from pathlib import Path
from typing import Any

from .state import MemoryHit

# AGENTS.md 고정 스택 — 임의로 바꾸지 않습니다.
EMBED_MODEL_ID = "amazon.titan-embed-text-v2:0"
BEDROCK_REGION = "us-east-1"
COLLECTION_NAME = "bug_patterns"

# 이 거리보다 가까우면 "같은 패턴" 으로 보고 기존 메모리에 병합합니다.
#
# 주의 — 이 값은 하드코딩해 두면 안 되는 **실험 파라미터**입니다.
# 너무 빡빡하면 같은 버그가 별개 패턴으로 쪼개져 전부 "1회" 로 남고,
# 너무 느슨하면 무관한 버그가 한 덩어리로 뭉쳐 발생 횟수가 부풀려집니다.
# 샘플을 돌려 보고 조정하십시오. (CLI 의 --merge-threshold)
#
# 0.3 → 0.55 로 조정한 근거(2026-09-03 온라인 실측, Titan v2 + Sonnet 4.5):
# py-index·py-dict·js-head 세 실행에서 나온 실제 거리 8개가 전부 0.3을 넘어
# 단 한 번도 병합되지 않았다 — "경계 검사 누락" 이 문구만 다르게 4번 따로 저장됐다.
#   같은 계열(진짜 병합돼야 함): 0.491, 0.524
#   다른 계열(분리돼야 함):      0.628, 0.635, 0.656, 0.714, 0.717, 0.725
# 0.55 는 그 둘 사이에 있다 — 같은 계열의 근접 거리는 병합하고 다른 계열은 여전히
# 분리한다. 표본이 8개뿐이므로 확정값이 아니라 갱신된 실험값이다.
DEFAULT_MERGE_THRESHOLD = 0.55

# Reciprocal Rank Fusion 의 관례값입니다. 상위 순위의 영향력을 완만하게 만드는
# 역할이고, 원 논문 이후 사실상 표준으로 쓰입니다. 조정 대상이 아닙니다.
RRF_K = 60


def _today() -> str:
    return date.today().isoformat()


def _tokenize(text: str) -> list[str]:
    """BM25 용 토큰화입니다.

    저장 문서는 ``# 경계 검사 누락 ## IndexError ### 범위 확인`` 처럼 한국어와 영어
    식별자가 섞여 있습니다. 그래서 두 가지를 함께 뽑습니다.

    - 낱말 단위 토큰 (영문 식별자·한글 단어·숫자)
    - ``IndexError`` 같은 CamelCase 를 쪼갠 조각 (``index``·``error``)

    후자가 중요합니다. 테스트 출력에는 ``IndexError`` 로, 저장된 요약에는 "인덱스 오류" 로
    적혀 있을 수 있어 낱말 그대로는 안 겹치는 경우가 있습니다.
    """
    words = re.findall(r"[A-Za-z][A-Za-z0-9_]*|[가-힣]+|\d+", text)
    tokens: list[str] = []
    for word in words:
        tokens.append(word.lower())
        parts = re.findall(r"[A-Z]+(?![a-z])|[A-Z][a-z]+|[a-z]+|\d+", word)
        if len(parts) > 1:
            tokens.extend(p.lower() for p in parts)
    return tokens


def _rrf_fuse(rankings: list[list[str]], k: int = RRF_K) -> list[tuple[str, float]]:
    """여러 순위 목록을 Reciprocal Rank Fusion 으로 합칩니다.

        score(d) = Σ 1 / (k + rank_i(d))

    **점수를 직접 더하지 않는 이유**가 있습니다. 코사인 거리와 BM25 점수는 서로 비교
    불가능한 척도입니다. 가중 합으로 섞으면 (1) 가중치라는 검증되지 않은 상수가 하나 더
    생기고, (2) BM25 점수 범위는 코퍼스에 따라 달라져 그 가중치가 데이터마다 어긋납니다.
    RRF 는 **순위만** 쓰므로 척도 문제가 사라지고 조정할 값이 없습니다.
    """
    scores: dict[str, float] = {}
    for ranking in rankings:
        for rank, doc_id in enumerate(ranking, start=1):
            scores[doc_id] = scores.get(doc_id, 0.0) + 1.0 / (k + rank)
    return sorted(scores.items(), key=lambda pair: pair[1], reverse=True)


def _best_rank(rankings: dict[str, list[str]]) -> dict[str, int]:
    """문서별로 **가장 좋았던** 순위를 뽑습니다. 확장 질의 중 하나라도 잘 잡았으면 인정합니다."""
    best: dict[str, int] = {}
    for ranking in rankings.values():
        for rank, doc_id in enumerate(ranking, start=1):
            if doc_id not in best or rank < best[doc_id]:
                best[doc_id] = rank
    return best


def _domain_boost(hit: MemoryHit, language: str) -> float:
    """도메인 신호로 순위를 보정합니다 (리랭킹).

    cross-encoder 리랭커를 쓰지 않는 이유는 ``sentence_transformers`` 의존성이 무겁고,
    LLM 리랭커는 토큰을 쓰며 **모델을 못 쓰는 축약 경로에서 동작하지 않기** 때문입니다.
    대신 이 도메인에만 있는 신호를 씁니다.

    - ``occurrences`` — 이 코드베이스가 **반복하는 실수**일수록 먼저 볼 만합니다.
      원본 노트북이 셀 수 없었던, 이 구현만의 신호입니다. 로그를 씌워 한 패턴이 순위를
      독점하지 않게 합니다.
    - **언어 일치** — ``--cross-language`` 로 격리를 풀었을 때 같은 언어를 위로 올립니다.
    - **최근성** — 오래 안 터진 패턴보다 최근 것이 지금 코드에 가깝습니다.

    **곱셈**으로 얹는 이유는 덧셈이면 보정이 원래 순위를 뒤집을 수 있기 때문입니다.
    곱셈이면 순위를 크게 흔들지 않으면서 동순위를 가릅니다.
    """
    boost = 1.0 + 0.1 * math.log1p(max(hit.occurrences - 1, 0))
    if language and hit.languages:
        langs = [v for v in hit.languages.split(",") if v]
        if language in langs:
            boost *= 1.05
    if hit.last_seen:
        try:
            days = (date.today() - date.fromisoformat(hit.last_seen)).days
        except ValueError:
            days = None
        if days is not None:
            # 30일을 반감기로 삼아 완만하게 깎습니다. 오래된 기록도 버리지는 않습니다.
            boost *= 1.0 + 0.05 * (0.5 ** (max(days, 0) / 30))
    return boost


def _merge_csv(existing: str, value: str) -> str:
    """콤마 구분 문자열에 값을 중복 없이 더합니다.

    Chroma metadata 는 스칼라만 담을 수 있어서 리스트를 이렇게 다룹니다.
    """
    items = [v for v in (existing or "").split(",") if v]
    if value and value not in items:
        items.append(value)
    return ",".join(items)


class TitanEmbeddingFunction:
    """langchain-aws 의 Titan 임베딩을 Chroma 가 요구하는 형태로 감쌉니다.

    **``embed_query`` 가 반드시 있어야 합니다.** 예전에는 ``__call__`` 만 두었는데,
    chromadb 는 **질의를 임베딩할 때 ``embedding_function.embed_query(input=...)`` 를
    호출**합니다. 그래서 저장(``add``)은 되는데 검색(``query``)은 항상
    ``AttributeError: 'TitanEmbeddingFunction' object has no attribute 'embed_query'`` 로
    실패했고, ``MemoryStore.search`` 의 ``except Exception: return []`` 가 그것을 조용히
    삼켜서 **메모리 주입이 한 번도 동작하지 않았습니다.** 이 프로젝트의 핵심 기여가
    검색 결과 주입인데, 그것이 침묵 속에 꺼져 있던 셈입니다.
    """

    def __init__(self, embeddings: Any = None):
        # AGENTS.md 규약: 모듈 최상단에서 실제 모델을 만들지 않습니다.
        # 여기서도 생성을 미루고, 주입받은 것이 있으면 그대로 씁니다.
        self._embeddings = embeddings

    def name(self) -> str:
        """Chroma 0.5+ 가 임베딩 함수 식별에 사용합니다."""
        return "titan-embed-text-v2"

    def _get(self):
        if self._embeddings is None:
            from langchain_aws import BedrockEmbeddings

            self._embeddings = BedrockEmbeddings(
                model_id=EMBED_MODEL_ID, region_name=BEDROCK_REGION
            )
        return self._embeddings

    def __call__(self, input: list[str]) -> list[list[float]]:  # noqa: A002 (Chroma 규약)
        return self._get().embed_documents(list(input))

    def embed_documents(self, input: list[str]) -> list[list[float]]:  # noqa: A002
        return self._get().embed_documents(list(input))

    def embed_query(self, input) -> list[list[float]]:  # noqa: A002
        """chromadb 가 **질의 임베딩에** 이 이름으로 호출합니다.

        chromadb 는 문자열 하나가 아니라 리스트를 넘기고 리스트를 기대합니다.
        문자열 하나가 와도 동작하게 방어해 둡니다.
        """
        texts = [input] if isinstance(input, str) else list(input)
        return self._get().embed_documents(texts)


class HashEmbeddings:
    """**검증용 오프라인 임베딩입니다. 실제 메모리에 쓰지 마십시오.**

    문장을 SHA-256 해시로 벡터화합니다. Bedrock 을 부르지 않으므로 자격증명 없이
    Chroma 경로 전체를 돌릴 수 있습니다. 예전에는 ``--no-memory`` 로 메모리를 꺼서
    오프라인을 보장했지만, 그 옵션을 없앤 뒤에는 이것을 주입해 같은 목적을 달성합니다.

    결정적(deterministic) 이어야 합니다 — 같은 문장이 매번 같은 벡터가 되어야
    "같은 패턴이면 가깝다" 를 재현할 수 있습니다. 다만 해시는 **의미를 보존하지
    않으므로**, 비슷한 뜻의 다른 문장은 가까워지지 않습니다. 그래서 검색 품질 평가에는
    쓸 수 없고, 배선·가드레일 검증에만 씁니다.
    """

    DIM = 32

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._one(t) for t in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._one(text)

    @classmethod
    def _one(cls, text: str) -> list[float]:
        digest = hashlib.sha256(text.encode("utf-8")).digest()
        raw = (digest * (cls.DIM // len(digest) + 1))[: cls.DIM]
        return [b / 255.0 for b in raw]


class MemoryStore:
    """버그 패턴 저장소입니다.

    메모리는 **항상 켜져 있습니다.** 예전에는 ``enabled=False`` 로 모든 연산을 no-op 으로
    만들어 ``--no-memory`` A/B 비교를 돌렸지만, 그 대조 구조를 없애면서 함께 제거했습니다.
    메모리 기여도는 ``report.json`` 의 ``injected`` 와 ``attempts`` 로 판단합니다.
    """

    def __init__(
        self,
        persist_dir: Path,
        *,
        embeddings: Any = None,
        merge_threshold: float = DEFAULT_MERGE_THRESHOLD,
        cross_language: bool = False,
    ):
        self.merge_threshold = merge_threshold
        self.cross_language = cross_language
        self._persist_dir = persist_dir
        self._embeddings = embeddings
        self._collection = None
        self._doc_cache: dict[str, dict[str, Any]] = {}
        """이번 프로세스에서 본 문서의 본문·거리·metadata 입니다.

        벡터 검색과 BM25 가 서로 다른 문서를 발견하므로, 융합 뒤 결과를 조립할 때
        한곳에서 꺼내 쓰기 위한 것입니다."""
        self.last_error = ""
        """마지막 검색이 실패한 사유. 조용한 실패를 드러내기 위한 값입니다."""

    # ── 지연 초기화 ──────────────────────────────────────────────
    @property
    def collection(self):
        """실제로 쓸 때까지 Chroma 도 Bedrock 도 건드리지 않습니다."""
        if self._collection is None:
            import chromadb

            self._persist_dir.mkdir(parents=True, exist_ok=True)
            client = chromadb.PersistentClient(path=str(self._persist_dir))
            self._collection = client.get_or_create_collection(
                name=COLLECTION_NAME,
                embedding_function=TitanEmbeddingFunction(self._embeddings),
                metadata={"hnsw:space": "cosine"},
            )
        return self._collection

    # ── 검색 ────────────────────────────────────────────────────
    def search(
        self,
        query: str | list[str],
        language: str,
        n_results: int = 3,
    ) -> list[MemoryHit]:
        """유사한 과거 버그 패턴을 찾습니다 — **하이브리드 + 리랭킹**.

        세 층으로 되어 있습니다(체크리스트 #3).

        1. **쿼리 확장** — ``query`` 에 목록을 넘기면 각각을 따로 검색합니다. 호출자가
           ``BugPattern`` 의 세 면(패턴명·증상·원인과해결)을 넘기면 "증상으로 찾기" 와
           "해결 방법으로 찾기" 가 각각 살아납니다. 한 문장으로 합쳐 던지면 세 면이
           평균되어 흐려집니다.
        2. **하이브리드** — 벡터 검색과 BM25 키워드 검색을 **각각** 돌려 RRF 로 합칩니다.
        3. **리랭킹** — ``occurrences``·언어 일치·최근성으로 곱셈 보정합니다.

        **문자열 하나도 그대로 받습니다.** 기존 호출부를 고치지 않아도 됩니다.

        기준선(벡터 단독) 순위를 ``MemoryHit.vector_rank`` 에 함께 남깁니다. 하이브리드가
        실제로 기여했는지 나중에 숫자로 판단하기 위한 것입니다 — 검증되지 않은 장치를
        얹지 않는다는 원칙(``--merge-threshold`` 에서 겪은 문제)에 따릅니다.
        """
        queries = [query] if isinstance(query, str) else [q for q in query if q]
        queries = [q.strip() for q in queries if q and q.strip()]
        if not queries:
            return []

        vector_ranking = self._vector_ranking(queries, language, n_results)
        if self.last_error:
            # 벡터 검색이 실패하면 거리 정보가 없어 병합 판정(merge_threshold)을 할 수
            # 없습니다. BM25 만으로 진행하면 거리 0 으로 오해되므로 여기서 끝냅니다.
            return []
        bm25_ranking = self._bm25_ranking(queries, language, n_results)

        fused = _rrf_fuse([r for r in (*vector_ranking.values(), *bm25_ranking.values())])
        if not fused:
            return []

        hits = self._build_hits(fused, vector_ranking, bm25_ranking, language, n_results)
        return hits

    def _vector_ranking(
        self, queries: list[str], language: str, n_results: int
    ) -> dict[str, list[str]]:
        """질의별 벡터 검색 순위입니다. 문서 메타데이터도 함께 채워 둡니다."""
        where = None if self.cross_language else {"language_primary": language}
        rankings: dict[str, list[str]] = {}
        # 확장 질의가 여러 개면 각각의 상위를 모아야 하므로 조금 넉넉히 가져옵니다.
        take = max(n_results * 2, n_results)
        for q in queries:
            try:
                results = self.collection.query(
                    query_texts=[q], n_results=take, where=where
                )
            except Exception as exc:
                # 메모리는 있으면 좋은 것이지 필수가 아닙니다.
                # 벡터DB 문제로 코드 수정 자체가 멈추면 안 됩니다.
                #
                # **다만 조용히 삼키지는 않습니다.** 예전에는 여기서 그냥 [] 를 돌려주었고,
                # 그래서 ``embed_query`` 누락으로 검색이 **항상** 실패하던 것을 아무도
                # 몰랐습니다. 사유를 남겨 호출자가 화면·리포트에 드러낼 수 있게 합니다.
                self.last_error = f"{type(exc).__name__}: {exc}"
                return {}

            ids = (results.get("ids") or [[]])[0]
            if not ids:
                continue
            docs = results["documents"][0]
            dists = results["distances"][0]
            metas = (results.get("metadatas") or [[{}] * len(ids)])[0]
            for i, mid in enumerate(ids):
                self._doc_cache[mid] = {
                    "document": docs[i],
                    "distance": float(dists[i]),
                    "meta": metas[i] or {},
                }
            rankings[q] = list(ids)

        self.last_error = ""
        return rankings

    def _bm25_ranking(
        self, queries: list[str], language: str, n_results: int
    ) -> dict[str, list[str]]:
        """질의별 BM25 키워드 순위입니다.

        벡터 검색은 의미가 비슷하면 잡지만 **정확한 단어 일치에는 약합니다.**
        ``IndexError`` 처럼 그 자체가 신호인 토큰은 키워드 검색이 더 잘 잡습니다.
        그래서 둘을 함께 씁니다(하이브리드).

        코퍼스는 매 호출마다 ``collection.get()`` 으로 만듭니다. 저장 문서는 한 줄
        요약이고 규모가 작아 문제가 없습니다. **수천 건이 되면 캐싱이 필요한 지점**입니다.
        """
        try:
            from rank_bm25 import BM25Okapi
        except ImportError:
            # 키워드 검색은 보조 장치입니다. 없으면 벡터 검색만으로 진행합니다.
            return {}

        try:
            where = None if self.cross_language else {"language_primary": language}
            data = self.collection.get(where=where)
        except Exception:
            return {}

        ids = list(data.get("ids") or [])
        docs = list(data.get("documents") or [])
        metas = list(data.get("metadatas") or [])
        if not ids:
            return {}
        for i, mid in enumerate(ids):
            self._doc_cache.setdefault(
                mid,
                {
                    "document": docs[i] if i < len(docs) else "",
                    # BM25 로만 발견된 문서는 벡터 거리를 모릅니다. 1.0(가장 먼 값)을 두어
                    # 병합 판정(merge_threshold)이 이것을 "같은 패턴" 으로 오인하지 않게 합니다.
                    "distance": 1.0,
                    "meta": (metas[i] if i < len(metas) else {}) or {},
                },
            )

        corpus = [_tokenize(d) for d in docs]
        if not any(corpus):
            return {}
        bm25 = BM25Okapi(corpus)

        rankings: dict[str, list[str]] = {}
        take = max(n_results * 2, n_results)
        for q in queries:
            tokens = _tokenize(q)
            if not tokens:
                continue
            scores = bm25.get_scores(tokens)
            order = sorted(range(len(ids)), key=lambda i: scores[i], reverse=True)
            # 점수가 0 이하인 문서는 순위에 넣지 않습니다.
            #
            # **코퍼스가 작으면 BM25 가 아무것도 돌려주지 않는 것이 정상입니다.**
            # IDF 는 "이 단어가 몇 개 문서에 있는가" 로 희소성을 재는데, 문서가 1~2건이면
            # 모든 단어가 사실상 전체 문서에 있어 IDF 가 붕괴합니다. 실측하면 1건일 때
            # -0.824, 2건일 때 0.0, 3건이 되어서야 1.532 가 나옵니다. 그래서 메모리가
            # 얼마 안 쌓인 초기에는 ``bm25_rank`` 가 ``None`` 인데, 버그가 아니라 이
            # 성질 때문입니다. 그때는 벡터 검색이 단독으로 일합니다.
            ranked = [ids[i] for i in order[:take] if scores[i] > 0]
            if ranked:
                rankings[f"bm25:{q}"] = ranked
        return rankings

    def _build_hits(
        self,
        fused: list[tuple[str, float]],
        vector_ranking: dict[str, list[str]],
        bm25_ranking: dict[str, list[str]],
        language: str,
        n_results: int,
    ) -> list[MemoryHit]:
        """융합 순위에 도메인 리랭킹을 얹어 최종 결과를 만듭니다."""
        vector_best = _best_rank(vector_ranking)
        bm25_best = _best_rank(bm25_ranking)

        scored: list[tuple[float, MemoryHit]] = []
        for mid, rrf in fused:
            cached = self._doc_cache.get(mid)
            if cached is None:
                continue
            meta = cached["meta"]
            hit = MemoryHit(
                id=mid,
                document=cached["document"],
                distance=cached["distance"],
                occurrences=int(meta.get("occurrences", 1)),
                languages=str(meta.get("languages", "")),
                first_seen=str(meta.get("first_seen", "")),
                last_seen=str(meta.get("last_seen", "")),
                pattern=str(meta.get("pattern", "")),
                vector_rank=vector_best.get(mid),
                bm25_rank=bm25_best.get(mid),
            )
            hit.boost = _domain_boost(hit, language)
            scored.append((rrf * hit.boost, hit))

        scored.sort(key=lambda pair: pair[0], reverse=True)
        hits = []
        for rank, (_, hit) in enumerate(scored[:n_results], start=1):
            hit.final_rank = rank
            hits.append(hit)
        return hits

    # ── 저장 ────────────────────────────────────────────────────
    def add(
        self, summary: str, language: str, target: str, *, pattern: str = ""
    ) -> str | None:
        """새 패턴을 저장합니다. 발생 횟수는 1 에서 시작합니다.

        ``pattern`` 은 구조화 출력(``BugPattern.pattern``)에서 온 패턴 이름입니다.
        metadata 에 따로 담아 두면 ``MemoryHit.title`` 이 문서 문자열을 다시 파싱하지
        않아도 됩니다. 구조화가 실패해 빈 문자열이면 기존 문자열 처리로 폴백합니다.
        """
        mid = str(uuid.uuid4())
        today = _today()
        self.collection.add(
            ids=[mid],
            documents=[summary],
            metadatas=[
                {
                    "occurrences": 1,
                    "first_seen": today,
                    "last_seen": today,
                    "language_primary": language,
                    "languages": language,
                    "targets": target,
                    "pattern": pattern,
                }
            ],
        )
        return mid

    def merge(
        self,
        memory_id: str,
        summary: str,
        language: str,
        target: str,
        *,
        pattern: str = "",
    ) -> int:
        """기존 패턴에 이번 사례를 합치고 **발생 횟수를 1 올립니다.**

        원본 노트북은 문서만 덮어썼기 때문에 "이 버그가 4번째다" 를 말할 수 없었습니다.
        누적 카운터가 생기면 self-healing 을 넘어서 **"이 코드베이스가 반복하는 실수"**
        리포트를 뽑을 수 있습니다. (``--stats``)
        """
        existing = self.collection.get(ids=[memory_id])
        metas = existing.get("metadatas") or [{}]
        meta = metas[0] if metas else {}
        # 덮어쓰기 전에 반드시 남깁니다. 이것이 유일한 복구 수단입니다.
        docs = existing.get("documents") or [""]
        self._log_merge(memory_id, docs[0] if docs else "", meta)
        occurrences = int(meta.get("occurrences", 1)) + 1
        self.collection.update(
            ids=[memory_id],
            documents=[summary],
            metadatas=[
                {
                    "occurrences": occurrences,
                    "first_seen": meta.get("first_seen", _today()),
                    "last_seen": _today(),
                    # 대표 언어는 최초 관측 언어를 유지해 격리 기준을 안정시킵니다.
                    "language_primary": meta.get("language_primary", language),
                    "languages": _merge_csv(str(meta.get("languages", "")), language),
                    "targets": _merge_csv(str(meta.get("targets", "")), target),
                    # 병합 결과의 패턴명. 구조화가 실패했으면 기존 값을 지키지 않고
                    # 덮어쓰지 않도록 이전 값을 유지합니다.
                    "pattern": pattern or str(meta.get("pattern", "")),
                }
            ],
        )
        return occurrences

    def _log_merge(self, memory_id: str, document: str, meta: dict) -> None:
        """병합으로 사라질 문서를 append-only 로그에 남깁니다.

        ``merge`` 는 ``collection.update`` 로 문서를 교체하므로, 남기지 않으면 이전 기록은
        영구 손실입니다. 코드에는 "승인 전까지 원본 불변"(G6) 을 걸어 두고 메모리에는 아무
        보호가 없던 비대칭을 메우는 장치입니다.

        부수 효과로 **반복 병합의 요약 손실을 관측할 자료**가 됩니다. N번째 병합은 이미
        N-1번 압축된 한 줄을 다시 압축하므로, 회차별 문서를 나란히 놓고 정보가 빠지는지
        확인할 수 있습니다.

        로그 쓰기가 실패해도 병합 자체는 진행합니다 — 보조 장치가 본 기능을 멈추게 하지
        않습니다(``search`` 의 예외 처리와 같은 원칙).
        """
        path = self._persist_dir.parent / ".heal" / "memory_history.jsonl"
        record = {
            "merged_at": datetime.now().isoformat(timespec="seconds"),
            "memory_id": memory_id,
            "document": document,
            "metadata": dict(meta or {}),
        }
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as fp:
                fp.write(json.dumps(record, ensure_ascii=False) + "\n")
        except OSError:
            pass

    def get_document(self, memory_id: str) -> str:
        docs = self.collection.get(ids=[memory_id]).get("documents") or [""]
        return docs[0] if docs else ""

    def delete(self, memory_id: str) -> None:
        """패턴 하나를 완전히 지웁니다. 잘못 쌓인 기록을 정리할 때만 씁니다.

        ``add``/``merge`` 와 달리 되돌릴 수 없습니다 — 검색 순위나 발생 횟수를
        낮추는 게 아니라 문서 자체를 없앱니다."""
        self.collection.delete(ids=[memory_id])

    # ── 누적 통계 ───────────────────────────────────────────────
    def stats(self) -> list[dict[str, Any]]:
        """발생 횟수 내림차순으로 전체 패턴을 돌려줍니다. ``--stats`` 의 재료입니다."""
        data = self.collection.get()
        rows: list[dict[str, Any]] = []
        for i, mid in enumerate(data.get("ids") or []):
            meta = (data.get("metadatas") or [{}])[i] or {}
            doc = (data.get("documents") or [""])[i]
            hit = MemoryHit(
                id=mid, document=doc, distance=0.0,
                pattern=str(meta.get("pattern", "")),
            )
            rows.append(
                {
                    "id": mid,
                    "pattern": hit.title,
                    "occurrences": int(meta.get("occurrences", 1)),
                    "languages": str(meta.get("languages", "")),
                    "first_seen": str(meta.get("first_seen", "")),
                    "last_seen": str(meta.get("last_seen", "")),
                }
            )
        rows.sort(key=lambda r: r["occurrences"], reverse=True)
        return rows
