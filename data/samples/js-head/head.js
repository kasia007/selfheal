// 교육용 더미 데이터입니다. 일부러 버그를 심어 둔 파일입니다.
//
// 버그 계열: 빈 배열의 첫 원소 접근 → undefined 역참조.
// 파이썬 샘플들과 **같은 계열이지만 언어가 다릅니다.**
// --cross-language 로 언어 격리를 풀었을 때 패턴이 전이되는지 보는 실험 재료입니다.

export function headUpper(list) {
  return list[0].toUpperCase();
}
