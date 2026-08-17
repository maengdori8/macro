"use strict";

// 프로 운영 설정(0:2 자동 종료 비율) — 저장·전파 규칙을 고정한다.
//
// 지켜야 할 것:
//   1) 범위 밖·형식 불량 값은 저장도 전파도 안 된다(클라이언트가 조용히 기본값으로
//      돌아가 "조절이 안 된다"로 보이는 사고 방지)
//   2) 문서가 없으면 기본값 40% — 배포 직후·롤백 후에도 프로가 그대로 돈다
//   3) 비율은 macro_pro 문서에만 실린다(일반·mPause 는 기능 자체가 없다)
//   4) 읽기는 캐시된다(verify 마다 Firestore 를 읽어 할당량을 잠식하지 않게)

const assert = require("node:assert/strict");
const test = require("node:test");
const {
  DEFAULT_AUTO_EXIT_RATIO,
  autoExitRatioAppliesTo,
  normalizeAutoExitRatio,
  readAutoExitRatio,
  writeAutoExitRatio,
} = require("../lib/pro_settings");
const { FakeFirestore } = require("./fake_firestore");

// ─── 값 검증 ───────────────────────────────────────────────────────────────

test("0~1 사이 숫자만 통과한다", () => {
  assert.equal(normalizeAutoExitRatio(0), 0);
  assert.equal(normalizeAutoExitRatio(0.4), 0.4);
  assert.equal(normalizeAutoExitRatio(1), 1);
  assert.equal(normalizeAutoExitRatio("0.35"), 0.35);
});

test("범위 밖·형식 불량은 전부 null 이다", () => {
  for (const bad of [-0.1, 1.5, NaN, Infinity, -Infinity, "", " ", "abc", null, undefined, true, false, {}, []]) {
    assert.equal(normalizeAutoExitRatio(bad), null, `통과되면 안 되는 값: ${String(bad)}`);
  }
});

test("비율은 macro_pro 에만 적용된다", () => {
  assert.equal(autoExitRatioAppliesTo("macro_pro"), true);
  assert.equal(autoExitRatioAppliesTo("macro"), false);
  assert.equal(autoExitRatioAppliesTo("mpause"), false);
  assert.equal(autoExitRatioAppliesTo(""), false);
  assert.equal(autoExitRatioAppliesTo(undefined), false);
});

// ─── 읽기 ──────────────────────────────────────────────────────────────────

test("문서가 없으면 기본값 40% 다", async () => {
  const db = new FakeFirestore();
  assert.equal(await readAutoExitRatio(db), DEFAULT_AUTO_EXIT_RATIO);
});

test("저장된 값이 깨져 있어도 기본값으로 돌아간다", async () => {
  const db = new FakeFirestore({ "config/pro_settings": { autoExitRatio: 7 } });
  assert.equal(await readAutoExitRatio(db), DEFAULT_AUTO_EXIT_RATIO);
});

test("읽기는 TTL 캐시를 쓴다 — verify 마다 Firestore 를 읽지 않는다", async () => {
  const db = new FakeFirestore({ "config/pro_settings": { autoExitRatio: 0.2 } });
  const t0 = 1_000_000;
  assert.equal(await readAutoExitRatio(db, t0), 0.2);

  // 문서를 몰래 바꿔도 TTL 안에서는 캐시된 값이 나온다(= 재읽기하지 않았다는 증거).
  db._docs.set("config/pro_settings", { autoExitRatio: 0.9 });
  assert.equal(await readAutoExitRatio(db, t0 + 1000), 0.2);

  // TTL 이 지나면 새 값을 읽는다.
  assert.equal(await readAutoExitRatio(db, t0 + 61 * 1000), 0.9);
});

// ─── 쓰기 ──────────────────────────────────────────────────────────────────

test("저장하면 같은 인스턴스가 즉시 새 값을 읽는다(캐시 무효화)", async () => {
  const db = new FakeFirestore();
  const t0 = 1_000_000;
  assert.equal(await readAutoExitRatio(db, t0), DEFAULT_AUTO_EXIT_RATIO);

  assert.equal(await writeAutoExitRatio(db, 0.25, t0), 0.25);
  assert.equal(db.read("config/pro_settings").autoExitRatio, 0.25);
  assert.equal(db.read("config/pro_settings").updatedAt, t0);
  // TTL 이 안 지났어도 쓰기 직후에는 새 값이다.
  assert.equal(await readAutoExitRatio(db, t0 + 1000), 0.25);
});

test("잘못된 값은 저장 자체가 거부된다", async () => {
  const db = new FakeFirestore();
  for (const bad of [1.5, -0.1, NaN, "abc", null]) {
    await assert.rejects(() => writeAutoExitRatio(db, bad), /0~1/);
  }
  assert.equal(db.read("config/pro_settings"), undefined, "거부된 값이 저장됐다");
});

test("merge 저장이라 미래에 추가될 다른 설정 필드를 지우지 않는다", async () => {
  const db = new FakeFirestore({ "config/pro_settings": { autoExitRatio: 0.4, futureField: "keep" } });
  await writeAutoExitRatio(db, 0.5);
  assert.equal(db.read("config/pro_settings").futureField, "keep");
  assert.equal(db.read("config/pro_settings").autoExitRatio, 0.5);
});
