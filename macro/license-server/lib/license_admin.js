"use strict";

const {
  buildPendingLicenseDocument,
  createSecureLicenseKey,
  getLicenseLifecycle,
  isCanonicalLicenseKey,
} = require("./license_lifecycle");

const MAX_BATCH_COUNT = 100;
const REQUEST_ID_PATTERN = /^[A-Za-z0-9_-]{8,80}$/;

class LicenseInputError extends Error {
  constructor(message) {
    super(message);
    this.name = "LicenseInputError";
    this.code = "LICENSE_INPUT";
  }
}

function normalizeBatchInput(body) {
  const days = Number(body?.days);
  const count = Number(body?.count);
  const requestId = String(body?.requestId || "").trim();
  const batchName = String(body?.batchName || "").trim();
  const memo = String(body?.memo || "").trim();

  if (!Number.isInteger(days) || (days !== 99999 && (days < 1 || days > 36500))) {
    throw new LicenseInputError("기간은 1~36500일 또는 무제한(99999)이어야 합니다.");
  }
  if (!Number.isInteger(count) || count < 1 || count > MAX_BATCH_COUNT) {
    throw new LicenseInputError(`발급 수량은 1~${MAX_BATCH_COUNT}개여야 합니다.`);
  }
  if (!REQUEST_ID_PATTERN.test(requestId)) {
    throw new LicenseInputError("재시도 방지 ID 형식이 올바르지 않습니다.");
  }
  if (batchName.length > 80) {
    throw new LicenseInputError("배치명은 80자 이하여야 합니다.");
  }
  if (memo.length > 200) {
    throw new LicenseInputError("메모는 200자 이하여야 합니다.");
  }
  return { days, count, requestId, batchName, memo };
}

async function issueLicenseBatch({
  db,
  input,
  now = Date.now(),
  timestampFromMillis,
  keyFactory = createSecureLicenseKey,
}) {
  const normalized = normalizeBatchInput(input);
  const batchRef = db.collection("licenseBatches").doc(normalized.requestId);

  return db.runTransaction(async (tx) => {
    const existingBatch = await tx.get(batchRef);
    if (existingBatch.exists) {
      const data = existingBatch.data();
      return {
        batchId: normalized.requestId,
        days: data.days,
        count: data.count,
        batchName: data.batchName || "",
        keys: Array.isArray(data.keys) ? data.keys : [],
        repeated: true,
      };
    }

    const keys = [];
    const keySet = new Set();
    while (keys.length < normalized.count) {
      const candidate = keyFactory();
      if (!isCanonicalLicenseKey(candidate) || keySet.has(candidate)) continue;
      keySet.add(candidate);
      keys.push(candidate);
    }

    const issuedAt = timestampFromMillis(now);
    for (const key of keys) {
      const ref = db.collection("licenses").doc(key);
      tx.create(
        ref,
        buildPendingLicenseDocument({
          days: normalized.days,
          issuedAt: now,
          timestampFromMillis,
          memo: normalized.memo,
          batchId: normalized.requestId,
          batchName: normalized.batchName,
        })
      );
    }
    tx.create(batchRef, {
      days: normalized.days,
      count: keys.length,
      batchName: normalized.batchName,
      memo: normalized.memo,
      keys,
      createdAt: issuedAt,
    });

    return {
      batchId: normalized.requestId,
      days: normalized.days,
      count: keys.length,
      batchName: normalized.batchName,
      keys,
      repeated: false,
    };
  });
}

function serializeLicenseForAdmin(key, data, now = Date.now()) {
  const lifecycle = getLicenseLifecycle(data, now);
  return {
    key,
    days: lifecycle.days,
    status: lifecycle.status,
    pending: lifecycle.pending,
    disabled: lifecycle.disabled,
    expired: lifecycle.expired,
    unlimited: lifecycle.unlimited,
    issuedAt: lifecycle.issuedAt,
    activatedAt: lifecycle.activatedAt || null,
    expiresAt: lifecycle.expiresAt || null,
    remainingDays: lifecycle.pending ? lifecycle.days : lifecycle.remainingDays,
    memo: data.memo || "",
    hwids: Array.isArray(data.hwids) ? data.hwids : [],
    maxHwids: data.maxHwids || 3,
    discordId: data.discordId || "",
    batchId: data.batchId || "",
    batchName: data.batchName || "",
  };
}

module.exports = {
  LicenseInputError,
  MAX_BATCH_COUNT,
  normalizeBatchInput,
  issueLicenseBatch,
  serializeLicenseForAdmin,
};
