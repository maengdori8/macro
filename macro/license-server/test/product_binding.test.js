"use strict";

// 제품(product) 바인딩 — "이 키는 이 앱 전용" 이 실제로 성립하는지 검증한다.
//
// 지켜야 할 두 방향:
//   1) mAuto 키로 mPause 를 열 수 없다 (신규 매출 보호)
//   2) 이미 배포된 mAuto 클라이언트는 아무것도 안 바뀐다 (기존 매출 보호)

const assert = require("node:assert/strict");
const test = require("node:test");
const {
  DAY_MS,
  FIRST_VERIFY_POLICY,
  buildPendingLicenseDocument,
} = require("../lib/license_lifecycle");
const { serializeLicenseForAdmin } = require("../lib/license_admin");
const { verifyAndActivateLicense } = require("../lib/license_service");
const { buildSignedMessage } = require("../lib/licenseSign");
const {
  DEFAULT_PRODUCT,
  isValidProduct,
  licenseProductOf,
  normalizeProduct,
  requestedProductOf,
} = require("../lib/product");
const { FakeFirestore } = require("./fake_firestore");

const KEY = "ABCDE-FGHJK-LMNPQ-RSTUV-WXYZ2";
const HWID = "0123456789abcdef0123456789abcdef";
const NONCE = "beefcafebeefcafe";

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

const NOW = Date.UTC(2026, 6, 25, 12);
const verify = (db, product) =>
  verifyAndActivateLicense({
    db,
    key: KEY,
    hwid: HWID,
    product,
    now: NOW,
    timestampFromMillis: (value) => value,
  });

// ─── 제품 식별자 해석 ──────────────────────────────────────────────────────

test("product 필드가 없는 기존 문서는 macro 로 해석된다(백필 불필요)", () => {
  assert.equal(licenseProductOf({}), DEFAULT_PRODUCT);
  assert.equal(licenseProductOf({ product: "" }), DEFAULT_PRODUCT);
  assert.equal(licenseProductOf({ product: "  mPause " }), "mpause");
  // 요청에 product 가 없으면 구버전 mAuto 클라이언트다.
  assert.equal(requestedProductOf(undefined), DEFAULT_PRODUCT);
  assert.equal(requestedProductOf("mpause"), "mpause");
});

test("구분자 주입이 가능한 product 는 형식 검사에서 걸린다", () => {
  assert.equal(isValidProduct("mpause"), true);
  assert.equal(isValidProduct("m-pause_2"), true);
  assert.equal(isValidProduct("valid|mpause"), false);   // '|' 주입
  assert.equal(isValidProduct("mPause"), false);         // 정규화 전 대문자
  assert.equal(isValidProduct(""), false);
  assert.equal(isValidProduct("a".repeat(33)), false);
  assert.equal(normalizeProduct("  MPAUSE "), "mpause");
});

// ─── 교차 사용 차단 ────────────────────────────────────────────────────────

test("mAuto 키로는 mPause 를 열 수 없다", async () => {
  const db = new FakeFirestore({ [`licenses/${KEY}`]: license() }); // product 없음 = macro
  const result = await verify(db, "mpause");
  assert.equal(result.valid, false);
  assert.match(result.message, /이 제품용 라이센스 키가 아닙니다/);
});

test("mPause 키로는 구버전 mAuto 클라이언트가 인증할 수 없다", async () => {
  const db = new FakeFirestore({ [`licenses/${KEY}`]: license({ product: "mpause" }) });
  const result = await verify(db, undefined); // 구버전은 product 를 안 보낸다
  assert.equal(result.valid, false);
  assert.match(result.message, /이 제품용 라이센스 키가 아닙니다/);
});

test("제품이 맞으면 정상 인증된다", async () => {
  const db = new FakeFirestore({ [`licenses/${KEY}`]: license({ product: "mpause" }) });
  const result = await verify(db, "mpause");
  assert.equal(result.valid, true);
  assert.equal(db.read(`licenses/${KEY}`).activatedAt, NOW);
});

test("기존 mAuto 키 + 구버전 클라이언트는 아무것도 바뀌지 않는다", async () => {
  const db = new FakeFirestore({ [`licenses/${KEY}`]: license() });
  const result = await verify(db, undefined);
  assert.equal(result.valid, true);
  const stored = db.read(`licenses/${KEY}`);
  assert.equal(stored.activatedAt, NOW);
  assert.equal(stored.expiresAt, NOW + 30 * DAY_MS);
  assert.deepEqual(stored.hwids, [HWID]);
});

// ─── 제품 불일치는 라이센스를 소모하지 않는다 ──────────────────────────────

test("제품이 다르면 유효기간 시계도, 기기 슬롯도 소모되지 않는다", async () => {
  // 이게 깨지면: 다른 앱이 키를 한 번 잘못 확인하는 것만으로 고객의 1일권이
  // 시작돼 버리거나 3대 한도 중 1대가 날아간다.
  const db = new FakeFirestore({ [`licenses/${KEY}`]: license({ product: "mpause" }) });
  const result = await verify(db, "macro");
  assert.equal(result.valid, false);
  const stored = db.read(`licenses/${KEY}`);
  assert.equal(stored.activatedAt, null, "유효기간이 시작되면 안 된다");
  assert.equal(stored.expiresAt, null);
  assert.deepEqual(stored.hwids, [], "기기가 등록되면 안 된다");
});

// ─── 발급 문서에 제품이 실제로 박히는가 ────────────────────────────────────

test("새 라이센스 문서는 product 를 저장하고, 생략하면 macro 다", () => {
  const stamp = (value) => value;
  const withoutProduct = buildPendingLicenseDocument({ days: 30, issuedAt: 1, timestampFromMillis: stamp });
  assert.equal(withoutProduct.product, DEFAULT_PRODUCT);

  const withProduct = buildPendingLicenseDocument({
    days: 30, issuedAt: 1, timestampFromMillis: stamp, product: "  MPause ",
  });
  assert.equal(withProduct.product, "mpause", "대소문자·공백은 정규화되어야 한다");
});

test("관리 화면 목록에 제품이 노출된다(값 없는 기존 키는 macro)", () => {
  const base = { days: 30, issuedAt: 0, activationPolicy: FIRST_VERIFY_POLICY, hwids: [], maxHwids: 3 };
  assert.equal(serializeLicenseForAdmin("K", base, NOW).product, DEFAULT_PRODUCT);
  assert.equal(
    serializeLicenseForAdmin("K", { ...base, product: "mpause" }, NOW).product,
    "mpause"
  );
});

// ─── 서명 메시지 ──────────────────────────────────────────────────────────

test("product 가 없으면 v1, 있으면 v2 로 서명한다", () => {
  assert.equal(
    buildSignedMessage({ verdict: "valid", hwid: HWID, nonce: NONCE, exp: 42 }),
    `license-v1|valid|${HWID}|${NONCE}|42`
  );
  assert.equal(
    buildSignedMessage({ product: "mpause", verdict: "valid", hwid: HWID, nonce: NONCE, exp: 42 }),
    `license-v2|mpause|valid|${HWID}|${NONCE}|42`
  );
});

test("v1 과 v2 메시지는 서로 재사용될 수 없다", () => {
  // mAuto 서버 응답을 그대로 mPause 클라이언트에 흘려도 메시지가 달라 검증에 실패한다.
  const v1 = buildSignedMessage({ verdict: "valid", hwid: HWID, nonce: NONCE, exp: 42 });
  const v2 = buildSignedMessage({ product: "mpause", verdict: "valid", hwid: HWID, nonce: NONCE, exp: 42 });
  assert.notEqual(v1, v2);
  assert.equal(v1.startsWith("license-v1|"), true);
  assert.equal(v2.startsWith("license-v2|"), true);
});

test("잘못된 product 는 서명 단계에서 거부된다", () => {
  assert.throws(
    () => buildSignedMessage({ product: "a|b", verdict: "valid", hwid: HWID, nonce: NONCE, exp: 1 }),
    /product 형식/
  );
});
