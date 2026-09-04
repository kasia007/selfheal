"""교육용 더미 데이터입니다.

이 파일이 **명세이자 성공 판정 기준**입니다.
에이전트는 이 파일을 절대 수정하지 못합니다. (adapters.py 의 test_file_patterns)
열어 주면 LLM 이 테스트를 고쳐서 통과시켜 버리기 때문입니다.
"""

from boundary import double_at


def test_정상_인덱스():
    assert double_at([1, 2, 3], 1) == 4


def test_범위를_벗어나면_0():
    """여기가 지금 깨져 있습니다. IndexError 가 납니다."""
    assert double_at([1, 2, 3], 5) == 0


def test_음수_인덱스도_경계로_취급():
    assert double_at([1, 2, 3], -10) == 0


def test_리스트가_없으면_0():
    assert double_at(None, 1) == 0
