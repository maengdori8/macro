"use strict";

const {
  buildFirstActivationUpdate,
  getLicenseLifecycle,
} = require("./license_lifecycle");
const {
  licenseProductOf,
  productSatisfies,
  requestedProductOf,
} = require("./product");

const DEFAULT_MAX_HWIDS = 3;

async function verifyAndActivateLicense({
  db,
  key,
  hwid,
  product,
  now = Date.now(),
  timestampFromMillis,
}) {
  const docRef = db.collection("licenses").doc(key);

  return db.runTransaction(async (tx) => {
    const doc = await tx.get(docRef);
    if (!doc.exists) {
      return { valid: false, message: "존재하지 않는 라이센스 키입니다." };
    }

    const data = doc.data();

    // 제품 확인은 활성화(=기간 시작)와 기기 등록보다 **먼저** 해야 한다.
    // 뒤에 두면, 다른 앱이 실수로 이 키를 검증하는 것만으로 유효기간 시계가
    // 시작되거나 기기 슬롯이 하나 소모된다.
    //
    // 정확히 같은지가 아니라 '만족시키는지'를 본다 — 프로 키는 일반 요청도
    // 만족시킨다(상위 티어 포함). 반대는 성립하지 않는다.
    const licenseProduct = licenseProductOf(data);
    if (!productSatisfies(requestedProductOf(product), licenseProduct)) {
      return { valid: false, message: "이 제품용 라이센스 키가 아닙니다." };
    }

    let lifecycle = getLicenseLifecycle(data, now);
    if (lifecycle.disabled) {
      return { valid: false, message: "비활성화된 라이센스 키입니다." };
    }
    if (lifecycle.expired) {
      return { valid: false, message: "만료된 라이센스 키입니다." };
    }

    const hwids = Array.isArray(data.hwids) ? [...data.hwids] : [];
    const maxHwids = Number(data.maxHwids || DEFAULT_MAX_HWIDS);
    const updates = {};

    if (!hwids.includes(hwid)) {
      if (hwids.length >= maxHwids) {
        return {
          valid: false,
          message: `기기 등록 한도 초과 (${maxHwids}대). 관리자에게 초기화를 요청하세요.`,
        };
      }
      hwids.push(hwid);
      updates.hwids = hwids;
    }

    Object.assign(
      updates,
      buildFirstActivationUpdate(data, now, timestampFromMillis)
    );
    if (Object.keys(updates).length > 0) {
      tx.update(docRef, updates);
      lifecycle = getLicenseLifecycle({ ...data, ...updates }, now);
    }

    return {
      valid: true,
      // 서명에는 **문서의 실제 제품**이 들어가야 한다. 요청한 제품을 넣으면
      // 프로 키로 일반 요청을 만족시켰을 때 클라이언트가 티어를 알 수 없다
      // (그리고 티어는 서명에 묶여 있어야 위조가 불가능하다).
      product: licenseProduct,
      message: lifecycle.unlimited
        ? "유효한 라이센스입니다. (무제한)"
        : `유효한 라이센스입니다. (${lifecycle.term}, ${lifecycle.remainingText})`,
      days: lifecycle.days,
      hours: lifecycle.hours,
      term: lifecycle.term,
      unlimited: lifecycle.unlimited,
      pending: lifecycle.pending,
      remainingDays: lifecycle.remainingDays,
      remainingText: lifecycle.remainingText,
      expiresAt: lifecycle.expiresAt || null,
      signatureExpiresAt: lifecycle.signatureExpiresAt || null,
      activatedAt: lifecycle.activatedAt || null,
    };
  }, { maxAttempts: 3 });
}

module.exports = {
  DEFAULT_MAX_HWIDS,
  verifyAndActivateLicense,
};
