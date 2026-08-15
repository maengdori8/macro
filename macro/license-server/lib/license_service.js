"use strict";

const {
  buildFirstActivationUpdate,
  getLicenseLifecycle,
} = require("./license_lifecycle");

const DEFAULT_MAX_HWIDS = 3;

async function verifyAndActivateLicense({
  db,
  key,
  hwid,
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
  });
}

module.exports = {
  DEFAULT_MAX_HWIDS,
  verifyAndActivateLicense,
};
