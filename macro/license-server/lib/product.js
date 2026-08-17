"use strict";

// 제품(product) 식별자 — 한 라이센스 서버로 여러 앱을 파는 근거.
//
// 왜 필요한가: v1 서명 메시지 "license-v1|{verdict}|{hwid}|{nonce}|{exp}" 에는
// 어떤 제품인지가 없다. 그래서 새 앱이 같은 서버·같은 공개키를 그대로 쓰면
// mAuto 라이센스 키가 새 앱에서도 그대로 통과한다. 제품을 서명에 묶어야
// "이 키는 이 앱 전용" 이 성립한다.
//
// 마이그레이션 규칙: 기존 라이센스 문서에는 product 필드가 없다. 없으면
// "macro"(mAuto)로 본다 → **백필 없이** 기존 키가 전부 그대로 동작한다.

const DEFAULT_PRODUCT = "macro";

// 서명 메시지의 구분자 '|' 주입을 원천 차단한다(클라이언트 정규식과 동일).
const PRODUCT_PATTERN = /^[a-z0-9_-]{1,32}$/;

// 실제로 **발급할 수 있는** 제품 목록.
//
// 검증(verify)은 위 패턴만 본다 — 모르는 제품이 오면 어차피 문서와 안 맞아 거부되고,
// 목록을 좁히면 새 앱을 낼 때마다 서버를 먼저 고쳐야 해서 배포 순서가 꼬인다.
// 반면 **발급은 목록을 좁혀야 한다.** 오타 하나("macropro")로 그 어떤 앱에서도 열리지
// 않는 키가 조용히 만들어지고, 고객 손에 들어가기 전까지 아무도 모르기 때문이다.
// (관리 화면은 드롭다운이라 안전하지만 license_generator.py 는 --product 를 직접 받는다.)
//
// 새 제품을 추가할 때: 여기 + 클라이언트의 PRODUCT_ID 상수 + 관리 화면 드롭다운/라벨.
const ISSUABLE_PRODUCTS = Object.freeze([
  "macro",      // mAuto — 감독모드 매크로
  "macro_pro",  // mAuto Pro — 종료 기능 포함(클라이언트 준비 중)
  "mpause",     // mPause — 종료 기능 단품
]);

function isIssuableProduct(value) {
  return ISSUABLE_PRODUCTS.includes(normalizeProduct(value));
}

// 어떤 요청을 어떤 제품의 키가 만족시키는가.
//
// 상위 티어는 하위를 포함한다: 프로 키는 일반 요청도 만족시킨다. 그래야 프로 키를
// 구버전(또는 일반) 클라이언트에 넣어도 '일반으로' 동작하고, 프로 클라이언트가
// 나오면 같은 키가 그대로 프로가 된다 — 키를 다시 발급할 필요가 없다.
//
// ⚠️ 반대는 절대 안 된다. 일반 키로 프로 요청을 만족시키면 티어 구분이 사라진다.
// mPause 는 완전히 분리된 제품이라 어느 쪽과도 섞이지 않는다.
const PRODUCT_SATISFIES = Object.freeze({
  macro: Object.freeze(["macro", "macro_pro"]),
  macro_pro: Object.freeze(["macro_pro"]),
  mpause: Object.freeze(["mpause"]),
});

function productSatisfies(requested, licenseProduct) {
  const want = normalizeProduct(requested) || DEFAULT_PRODUCT;
  const have = normalizeProduct(licenseProduct) || DEFAULT_PRODUCT;
  // 모르는 제품은 정확히 일치할 때만 통과시킨다(허용목록에 없는 값이 넓게 열리면 안 된다).
  const allowed = PRODUCT_SATISFIES[want] || [want];
  return allowed.includes(have);
}

function normalizeProduct(value) {
  return String(value === undefined || value === null ? "" : value)
    .trim()
    .toLowerCase();
}

function isValidProduct(value) {
  return PRODUCT_PATTERN.test(value);
}

// 라이센스 문서가 어떤 제품용인지. 필드가 없으면 기존 mAuto 키다.
function licenseProductOf(data) {
  return normalizeProduct(data && data.product) || DEFAULT_PRODUCT;
}

// 요청이 어떤 제품을 원하는지. 비어 있으면 구버전 mAuto 클라이언트다.
function requestedProductOf(value) {
  return normalizeProduct(value) || DEFAULT_PRODUCT;
}

module.exports = {
  DEFAULT_PRODUCT,
  PRODUCT_PATTERN,
  ISSUABLE_PRODUCTS,
  PRODUCT_SATISFIES,
  normalizeProduct,
  isValidProduct,
  isIssuableProduct,
  productSatisfies,
  licenseProductOf,
  requestedProductOf,
};
