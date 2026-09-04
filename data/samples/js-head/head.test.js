// 교육용 더미 데이터입니다. 이 파일이 명세이자 성공 판정 기준입니다.
// 에이전트는 이 파일을 수정하지 못합니다.

import test from "node:test";
import assert from "node:assert/strict";
import { headUpper } from "./head.js";

test("정상 입력", () => {
  assert.equal(headUpper(["hello", "world"]), "HELLO");
});

test("빈 배열이면 빈 문자열", () => {
  // 여기가 지금 깨져 있습니다. undefined.toUpperCase() 로 터집니다.
  assert.equal(headUpper([]), "");
});

test("배열이 없으면 빈 문자열", () => {
  assert.equal(headUpper(null), "");
});
