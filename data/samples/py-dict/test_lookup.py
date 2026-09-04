"""교육용 더미 데이터입니다. 이 파일이 명세이자 성공 판정 기준입니다."""

from lookup import label_of


def test_정상_조회():
    assert label_of({"a": "hello"}, "a") == "HELLO"


def test_없는_키는_빈_문자열():
    """여기가 지금 깨져 있습니다. KeyError 가 납니다."""
    assert label_of({"a": "hello"}, "zzz") == ""


def test_매핑이_없으면_빈_문자열():
    assert label_of(None, "a") == ""


def test_값이_None_이면_빈_문자열():
    assert label_of({"a": None}, "a") == ""
