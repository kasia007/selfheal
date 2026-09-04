// 교육용 더미 데이터입니다.
//
// 이 파일이 명세이자 성공 판정 기준입니다.
// 에이전트는 이 파일을 절대 수정하지 못합니다. (adapters.py 의 test_file_patterns)
// 열어 주면 LLM 이 테스트를 고쳐서 통과시켜 버리기 때문입니다.

package sample

import "testing"

func Test_정상_인덱스(t *testing.T) {
	if got := DoubleAt([]int{1, 2, 3}, 1); got != 4 {
		t.Fatalf("want 4, got %d", got)
	}
}

func Test_범위를_벗어나면_0(t *testing.T) {
	// 여기가 지금 깨져 있습니다. index out of range 로 panic 이 납니다.
	if got := DoubleAt([]int{1, 2, 3}, 5); got != 0 {
		t.Fatalf("want 0, got %d", got)
	}
}

func Test_음수_인덱스도_경계로_취급(t *testing.T) {
	if got := DoubleAt([]int{1, 2, 3}, -10); got != 0 {
		t.Fatalf("want 0, got %d", got)
	}
}

func Test_슬라이스가_없으면_0(t *testing.T) {
	var items []int
	if got := DoubleAt(items, 1); got != 0 {
		t.Fatalf("want 0, got %d", got)
	}
}
