// 교육용 더미 데이터입니다. 일부러 버그를 심어 둔 파일입니다.
//
// 버그 계열: 경계 검사 누락 (범위를 벗어난 인덱스 접근)
// py-index(boundary.py)와 같은 계열이지만 언어가 다릅니다.
// --cross-language 로 언어 격리를 풀었을 때 패턴이 전이되는지 보는 실험 재료입니다.

package sample

// DoubleAt 은 슬라이스의 index 번째 값을 두 배로 돌려줍니다.
func DoubleAt(items []int, index int) int {
	return items[index] * 2
}
