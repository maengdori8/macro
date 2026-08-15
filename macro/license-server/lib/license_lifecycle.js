"use strict";

const crypto = require("crypto");

const DAY_MS = 24 * 60 * 60 * 1000;
const UNLIMITED_DAYS = 99999;
const FIRST_VERIFY_POLICY = "first_verify";
const KEY_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789";
const KEY_PATTERN = /^[ABCDEFGHJKLMNPQRSTUVWXYZ23456789]{5}(?:-[ABCDEFGHJKLMNPQRSTUVWXYZ23456789]{5}){4}$/;

function toMillis(value) {
  if (value === null || value === undefined) return 0;
  if (typeof value === "number") return Number.isFinite(value) ? value : 0;
  if (typeof value.toMillis === "function") return value.toMillis();
  if (value instanceof Date) return value.getTime();
  return 0;
}

function isFirstVerifyLicense(data) {
  return data?.activationPolicy === FIRST_VERIFY_POLICY;
}

function isUnlimited(data) {
  return Number(data?.days || 0) === UNLIMITED_DAYS;
}

function getLicenseLifecycle(data, now = Date.now()) {
  const days = Number(data?.days || 0);
  const unlimited = days === UNLIMITED_DAYS;
  const disabled = data?.disabled === true;
  const firstVerify = isFirstVerifyLicense(data);
  const activatedAt = toMillis(data?.activatedAt);
  const pending = firstVerify && activatedAt <= 0;

  let expiresAt = 0;
  if (!unlimited && !pending) {
    expiresAt = toMillis(data?.expiresAt);
    if (expiresAt <= 0) {
      const base = firstVerify ? activatedAt : toMillis(data?.createdAt);
      expiresAt = base > 0 ? base + days * DAY_MS : 0;
    }
  }

  const expired = !unlimited && !pending && (expiresAt <= 0 || now >= expiresAt);
  let status = "active";
  if (disabled) status = "disabled";
  else if (pending) status = "pending";
  else if (expired) status = "expired";

  const remainingDays =
    unlimited || pending
      ? null
      : Math.max(0, Math.ceil((expiresAt - now) / DAY_MS));

  return {
    status,
    disabled,
    pending,
    expired,
    unlimited,
    days,
    issuedAt: toMillis(data?.issuedAt) || toMillis(data?.createdAt),
    activatedAt: pending ? 0 : activatedAt || toMillis(data?.createdAt),
    expiresAt: unlimited || pending ? 0 : expiresAt,
    remainingDays,
  };
}

function createSecureLicenseKey() {
  const chars = [];
  for (let index = 0; index < 25; index += 1) {
    chars.push(KEY_ALPHABET[crypto.randomInt(0, KEY_ALPHABET.length)]);
  }
  return Array.from({ length: 5 }, (_, part) =>
    chars.slice(part * 5, part * 5 + 5).join("")
  ).join("-");
}

function isCanonicalLicenseKey(key) {
  return typeof key === "string" && KEY_PATTERN.test(key);
}

function buildPendingLicenseDocument({
  days,
  issuedAt,
  timestampFromMillis,
  memo = "",
  batchId = "",
  batchName = "",
  maxHwids = 3,
}) {
  const issuedTimestamp = timestampFromMillis(issuedAt);
  return {
    days,
    createdAt: issuedTimestamp,
    issuedAt: issuedTimestamp,
    activationPolicy: FIRST_VERIFY_POLICY,
    activatedAt: null,
    expiresAt: null,
    disabled: false,
    memo,
    batchId,
    batchName,
    hwids: [],
    maxHwids,
    discordId: "",
  };
}

function buildFirstActivationUpdate(data, now, timestampFromMillis) {
  const lifecycle = getLicenseLifecycle(data, now);
  if (!lifecycle.pending) return {};

  const activatedTimestamp = timestampFromMillis(now);
  const expiresAt =
    lifecycle.unlimited ? null : timestampFromMillis(now + lifecycle.days * DAY_MS);
  return {
    activatedAt: activatedTimestamp,
    createdAt: activatedTimestamp,
    expiresAt,
  };
}

function isMigratableUnusedLicense(data) {
  if (!data || isFirstVerifyLicense(data)) return false;
  if (toMillis(data.activatedAt) > 0) return false;
  if (data.hwids === undefined || data.hwids === null) return true;
  return Array.isArray(data.hwids) && data.hwids.length === 0;
}

function buildUnusedMigrationUpdate(data, now, timestampFromMillis) {
  const issuedAt = toMillis(data.issuedAt) || toMillis(data.createdAt) || now;
  return {
    issuedAt: timestampFromMillis(issuedAt),
    activationPolicy: FIRST_VERIFY_POLICY,
    activatedAt: null,
    expiresAt: null,
    migrationVersion: 1,
    migratedAt: timestampFromMillis(now),
  };
}

module.exports = {
  DAY_MS,
  UNLIMITED_DAYS,
  FIRST_VERIFY_POLICY,
  KEY_ALPHABET,
  toMillis,
  isFirstVerifyLicense,
  isUnlimited,
  getLicenseLifecycle,
  createSecureLicenseKey,
  isCanonicalLicenseKey,
  buildPendingLicenseDocument,
  buildFirstActivationUpdate,
  isMigratableUnusedLicense,
  buildUnusedMigrationUpdate,
};
