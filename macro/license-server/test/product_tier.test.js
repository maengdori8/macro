"use strict";

// 티어(일반 / 프로) — 같은 앱 안에서 기능을 가르는 근거가 서명에 묶이는지 검증한다.
//
// 규칙은 비대칭이다:
//   * 프로 키는 일반 요청도 만족시킨다(상위 포함) → 프로 고객이 구버전·일반
//     클라이언트에서도 막히지 않고, 프로 빌드가 나오면 같은 키가 그대로 프로가 된다.
//   * 일반 키는 프로 요청을 **절대** 만족시키지 못한다 → 이게 무너지면 티어가 없다.
//   * mPause 는 어느 쪽과도 섞이지 않는다.

const assert = require("node:assert/strict");
const test = require("node:test");
const { FIRST_VERIFY_POLICY } = require("../lib/license_lifecycle");
const { verifyAndActivateLicense } = require("../lib/license_service");
const { buildSignedMessage } = require("../lib/licenseSign");
const { productSatisfies, PRODUCT_SATISFIES } = require("../lib/product");
const { FakeFirestore } = require("./fake_firestore");

const KEY = "ABCDE-FGHJK-LMNPQ-RSTUV-WXYZ2";
const HWID = "0123456789abcdef0123456789abcdef";
const NOW = Date.UTC(2026, 6, 25, 12);

function license(extra = {}) {
  const issuedAt = Date.UTC(2026, 0, 1);
  return {
    days: 30,
    createdAt: issuedAt,
    issuedAt,
    activationPolicy: FIRST_VERIFY_POLICY,
    activatedAt: null,
    expiresAt: null,
    disabled: false,
    hwids: [],
    maxHwids: 3,
    ...extra,
  };
}

const verify = (db, product) =>
  verifyAndActivateLicense({
    db,
    key: KEY,
    hwid: HWID,
    product,
    now: NOW,
    timestampFromMillis: (value) => value,
  });

// ─── 만족 규칙 ─────────────────────────────────────────────────────────────

test("프로 키는 일반 요청을 만족시킨다(상위 포함)", () => {
  assert.equal(productSatisfies("macro", "macro_pro"), true);
  assert.equal(productSatisfies("macro", "macro"), true);
});

test("일반 키로는 프로 요청을 만족시킬 수 없다", () => {
  assert.equal(productSatisfies("macro_pro", "macro"), false);
  assert.equal(productSatisfies("macro_pro", "macro_pro"), true);
});

test("mPause 는 어느 쪽과도 섞이지 않는다", () => {
  for (const other of ["macro", "macro_pro"]) {
    assert.equal(productSatisfies("mpause", other), false);
    assert.equal(productSatisfies(other, "mpause"), false);
  }
  assert.equal(productSatisfies("mpause", "mpause"), true);
});

test("요청 제품이 비면 기존 mAuto 로 본다(구버전 클라이언트)", () => {
  assert.equal(productSatisfies("", "macro"), true);
  assert.equal(productSatisfies("", "macro_pro"), true, "프로 키가 구버전에서 막히면 안 된다");
  assert.equal(productSatisfies("", "mpause"), false);
});

test("모르는 제품은 정확히 일치할 때만 통과한다", () => {
  assert.equal(productSatisfies("unknown_app", "unknown_app"), true);
  assert.equal(productSatisfies("unknown_app", "macro"), false);
  assert.equal(productSatisfies("macro", "unknown_app"), false);
});

test("만족 표는 얼어 있고 프로가 일반을 포함하지 않는다", () => {
  assert.ok(Object.isFrozen(PRODUCT_SATISFIES));
  assert.deepEqual([...PRODUCT_SATISFIES.macro_pro], ["macro_pro"]);
});

// ─── 실제 검증 경로 ────────────────────────────────────────────────────────

test("프로 키로 일반 클라이언트가 인증된다", async () => {
  const db = new FakeFirestore({ [`licenses/${KEY}`]: license({ product: "macro_pro" }) });
  const result = await verify(db, "macro");
  assert.equal(result.valid, true);
  // 서명에는 **문서의 제품**이 들어가야 클라이언트가 티어를 읽을 수 있다.
  assert.equal(result.product, "macro_pro");
});

test("일반 키로 프로를 요구하면 거부된다", async () => {
  const db = new FakeFirestore({ [`licenses/${KEY}`]: license({ product: "macro" }) });
  const result = await verify(db, "macro_pro");
  assert.equal(result.valid, false);
  assert.match(result.message, /제품용/);
});

test("일반 키는 일반 요청에서 일반으로 서명된다", async () => {
  const db = new FakeFirestore({ [`licenses/${KEY}`]: license({ product: "macro" }) });
  const result = await verify(db, "macro");
  assert.equal(result.valid, true);
  assert.equal(result.product, "macro");
});

test("product 없는 옛 키도 일반으로 서명된다", async () => {
  const db = new FakeFirestore({ [`licenses/${KEY}`]: license() });
  const result = await verify(db, "macro");
  assert.equal(result.valid, true);
  assert.equal(result.product, "macro");
});

test("거부된 프로 요청은 기간 시계를 시작시키지 않는다", async () => {
  const db = new FakeFirestore({ [`licenses/${KEY}`]: license({ product: "macro" }) });
  await verify(db, "macro_pro");
  const stored = db.read(`licenses/${KEY}`);
  assert.equal(stored.activatedAt, null, "티어가 안 맞는데 활성화됐다");
  assert.deepEqual(stored.hwids, [], "티어가 안 맞는데 기기가 등록됐다");
});

// ─── 서명 메시지 ───────────────────────────────────────────────────────────

test("티어가 다르면 서명 메시지도 다르다(위조 불가)", () => {
  const base = { verdict: "valid", hwid: HWID, nonce: "beef", exp: 1 };
  const standard = buildSignedMessage({ ...base, product: "macro" });
  const pro = buildSignedMessage({ ...base, product: "macro_pro" });
  assert.notEqual(standard, pro);
  assert.match(pro, /^license-v2\|macro_pro\|/);
});

test("요청에 product 가 없으면 여전히 v1 이다(구버전 클라이언트 보호)", () => {
  const message = buildSignedMessage({
    product: "", verdict: "valid", hwid: HWID, nonce: "beef", exp: 1,
  });
  assert.match(message, /^license-v1\|/);
});
