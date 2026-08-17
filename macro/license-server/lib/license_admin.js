"use strict";

const term = require("./licenseTerm");
const {
  buildPendingLicenseDocument,
  createSecureLicenseKey,
  getLicenseLifecycle,
  isCanonicalLicenseKey,
} = require("./license_lifecycle");
const {
  DEFAULT_PRODUCT,
  ISSUABLE_PRODUCTS,
  isIssuableProduct,
  licenseProductOf,
  normalizeProduct,
} = require("./product");

const MAX_BATCH_COUNT = 100;
const REQUEST_ID_PATTERN = /^[A-Za-z0-9_-]{8,80}$/;

class LicenseInputError extends Error {
  constructor(message) {
    super(message);
    this.name = "LicenseInputError";
    this.code = "LICENSE_INPUT";
  }
}

function normalizeLicenseTerm(body) {
  const rawHours = body?.hours;
  const hasHours = rawHours !== undefined && rawHours !== null && String(rawHours) !== "";
  const hours = hasHours ? Number(rawHours) : 0;
  const days = Number(body?.days || 0);

  if (hasHours) {
    if (days !== 0) throw new LicenseInputError("일과 시간은 동시에 지정할 수 없습니다.");
    if (!Number.isInteger(hours) || hours < 1 || hours > term.MAX_ISSUE_HOURS) {
      throw new LicenseInputError(`시간권은 1~${term.MAX_ISSUE_HOURS}시간이어야 합니다.`);
    }
    return { days: 0, hours };
  }

  if (!Number.isInteger(days) || (days !== term.UNLIMITED_DAYS && (days < 1 || days > 36500))) {
    throw new LicenseInputError("기간은 1~36500일 또는 무제한(99999)이어야 합니다.");
  }
  return { days, hours: 0 };
}

function normalizeBatchInput(body) {
  const { days, hours } = normalizeLicenseTerm(body);
  const count = Number(body?.count);
  const requestId = String(body?.requestId || "").trim();
  const batchName = String(body?.batchName || "").trim();
  const memo = String(body?.memo || "").trim();
  // 어느 앱 전용 키인지. 지정하지 않으면 기존과 같이 mAuto("macro") 키가 나온다.
  const product = normalizeProduct(body?.product) || DEFAULT_PRODUCT;

  if (!Number.isInteger(count) || count < 1 || count > MAX_BATCH_COUNT) {
    throw new LicenseInputError(`발급 수량은 1~${MAX_BATCH_COUNT}개여야 합니다.`);
  }
  if (!REQUEST_ID_PATTERN.test(requestId)) {
    throw new LicenseInputError("재시도 방지 ID 형식이 올바르지 않습니다.");
  }
  if (batchName.length > 80) throw new LicenseInputError("배치명은 80자 이하여야 합니다.");
  if (memo.length > 200) throw new LicenseInputError("메모는 200자 이하여야 합니다.");
  // 발급은 아는 제품만. 오타(예: "macropro")로 만든 키는 어느 앱에서도 안 열리는데,
  // 그 사실이 고객 손에 들어가기 전까지 드러나지 않는다.
  if (!isIssuableProduct(product)) {
    throw new LicenseInputError(
      `발급할 수 없는 제품입니다. (${ISSUABLE_PRODUCTS.join(", ")})`
    );
  }
  return { days, hours, count, requestId, batchName, memo, product };
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
      const days = Number(data.days || 0);
      const hours = Number(data.hours || 0);
      return {
        batchId: normalized.requestId,
        days,
        hours,
        term: term.termText({ days, hours }),
        count: data.count,
        batchName: data.batchName || "",
        product: normalizeProduct(data.product) || DEFAULT_PRODUCT,
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
      tx.create(
        db.collection("licenses").doc(key),
        buildPendingLicenseDocument({
          days: normalized.days,
          hours: normalized.hours,
          issuedAt: now,
          timestampFromMillis,
          memo: normalized.memo,
          batchId: normalized.requestId,
          batchName: normalized.batchName,
          product: normalized.product,
        })
      );
    }
    tx.create(batchRef, {
      days: normalized.days,
      hours: normalized.hours,
      count: keys.length,
      batchName: normalized.batchName,
      memo: normalized.memo,
      product: normalized.product,
      keys,
      createdAt: issuedAt,
    });

    return {
      batchId: normalized.requestId,
      days: normalized.days,
      hours: normalized.hours,
      term: term.termText(normalized),
      count: keys.length,
      batchName: normalized.batchName,
      product: normalized.product,
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
    hours: lifecycle.hours,
    term: lifecycle.term,
    status: lifecycle.status,
    pending: lifecycle.pending,
    disabled: lifecycle.disabled,
    expired: lifecycle.expired,
    unlimited: lifecycle.unlimited,
    issuedAt: lifecycle.issuedAt,
    activatedAt: lifecycle.activatedAt || null,
    expiresAt: lifecycle.expiresAt || null,
    remainingDays: lifecycle.pending ? null : lifecycle.remainingDays,
    remainingText: lifecycle.remainingText,
    memo: data.memo || "",
    hwids: Array.isArray(data.hwids) ? data.hwids : [],
    maxHwids: data.maxHwids || 3,
    discordId: data.discordId || "",
    batchId: data.batchId || "",
    batchName: data.batchName || "",
    product: licenseProductOf(data),
  };
}

module.exports = {
  LicenseInputError,
  MAX_BATCH_COUNT,
  normalizeLicenseTerm,
  normalizeBatchInput,
  issueLicenseBatch,
  serializeLicenseForAdmin,
};
