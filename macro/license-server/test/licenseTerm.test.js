"use strict";

// 유효기간 계산 검증. 시간권을 추가하면서 기존 일권/무제한이 깨지지 않는지가 핵심이다.
// 실행: node license-server/test/licenseTerm.test.js

const assert = require("assert");
const term = require("../lib/licenseTerm");

const HOUR = 3600000;
const DAY = 86400000;
const T0 = 1_700_000_000_000; // 기준 생성 시각

let passed = 0;
function check(name, fn) {
  try {
    fn();
    passed += 1;
    console.log(`  OK   ${name}`);
  } catch (e) {
    console.log(`  FAIL ${name}\n       ${e.message}`);
    process.exitCode = 1;
  }
}

console.log("=== 기존 동작 보존(회귀 방지) ===");

check("일권 만료 시각이 예전 계산식과 동일", () => {
  const d = { days: 7, createdAt: T0 };
  assert.strictEqual(term.expiresAt(d), T0 + 7 * DAY);
});

check("hours 필드가 없는 기존 문서도 정상", () => {
  const d = { days: 30, createdAt: T0 }; // hours: undefined
  assert.strictEqual(term.expiresAt(d), T0 + 30 * DAY);
  assert.strictEqual(term.isExpired(d, T0 + 29 * DAY), false);
  assert.strictEqual(term.isExpired(d, T0 + 31 * DAY), true);
});

check("무제한은 유한한 먼 미래값(Infinity 금지)", () => {
  const d = { days: 99999, createdAt: T0 };
  const exp = term.expiresAt(d);
  assert.ok(Number.isFinite(exp), "Infinity면 서명 exp가 null이 되어 무제한 키가 깨진다");
  assert.strictEqual(exp, T0 + 99999 * DAY);
  assert.strictEqual(term.isUnlimited(d), true);
  assert.strictEqual(term.isExpired(d, T0 + 100 * DAY), false);
});

check("무제한 exp가 JSON 왕복에서 살아남음", () => {
  const d = { days: 99999, createdAt: T0 };
  const exp = Math.floor(term.expiresAt(d) / 1000);
  assert.strictEqual(JSON.parse(JSON.stringify({ exp })).exp, exp);
  assert.ok(Number.isInteger(exp));
});

console.log("=== 시간권 ===");

check("1시간권", () => {
  const d = { days: 0, hours: 1, createdAt: T0 };
  assert.strictEqual(term.expiresAt(d), T0 + HOUR);
  assert.strictEqual(term.termText(d), "1시간권");
});

check("24시간권", () => {
  const d = { days: 0, hours: 24, createdAt: T0 };
  assert.strictEqual(term.expiresAt(d), T0 + 24 * HOUR);
  assert.strictEqual(term.expiresAt(d), T0 + DAY, "24시간 = 1일");
});

check("시간권 만료 경계", () => {
  const d = { days: 0, hours: 3, createdAt: T0 };
  assert.strictEqual(term.isExpired(d, T0 + 3 * HOUR - 1000), false);
  assert.strictEqual(term.isExpired(d, T0 + 3 * HOUR + 1000), true);
});

check("hours가 days보다 우선", () => {
  const d = { days: 30, hours: 2, createdAt: T0 };
  assert.strictEqual(term.expiresAt(d), T0 + 2 * HOUR);
});

console.log("=== 표기 ===");

check("남은 시간 표기가 시간권에서 '일'로 뭉개지지 않음", () => {
  const d = { days: 0, hours: 3, createdAt: T0 };
  assert.strictEqual(term.remainingText(d, T0), "3시간 남음");
  assert.strictEqual(term.remainingText(d, T0 + 2.5 * HOUR), "30분 남음");
  assert.strictEqual(term.remainingText(d, T0 + 4 * HOUR), "만료됨");
});

check("일권/무제한 표기", () => {
  assert.strictEqual(term.termText({ days: 7 }), "7일권");
  assert.strictEqual(term.termText({ days: 99999 }), "무제한");
  assert.strictEqual(term.remainingText({ days: 99999, createdAt: T0 }, T0), "무제한");
});

check("Firestore Timestamp 객체도 처리", () => {
  const d = { days: 1, createdAt: { toMillis: () => T0 } };
  assert.strictEqual(term.expiresAt(d), T0 + DAY);
});

console.log("=== 연장·무제한 전환 시 hours 정리 ===");

check("시간권을 연장하면 hours가 비워져야 만료가 실제로 늘어남", () => {
  // admin.js가 { days: N, hours: 0 }으로 갱신한 뒤의 문서 상태를 재현한다.
  const before = { days: 0, hours: 3, createdAt: T0 };
  assert.strictEqual(term.expiresAt(before), T0 + 3 * HOUR);

  const afterBuggy = { days: 8, hours: 3, createdAt: T0 }; // hours를 안 지운 경우
  assert.strictEqual(
    term.expiresAt(afterBuggy),
    T0 + 3 * HOUR,
    "hours가 남으면 연장이 무시된다(이래서 admin이 hours:0을 함께 쓴다)"
  );

  const afterFixed = { days: 8, hours: 0, createdAt: T0 };
  assert.strictEqual(term.expiresAt(afterFixed), T0 + 8 * DAY);
});

check("시간권을 무제한으로 전환", () => {
  const buggy = { days: 99999, hours: 3, createdAt: T0 };
  assert.strictEqual(term.expiresAt(buggy), T0 + 99999 * DAY,
    "무제한은 hours보다 우선한다(isUnlimited 우선 판정)");
  const fixed = { days: 99999, hours: 0, createdAt: T0 };
  assert.strictEqual(term.isUnlimited(fixed), true);
});

console.log(`\n${passed}개 통과${process.exitCode ? " (실패 있음)" : ""}`);
