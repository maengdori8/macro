"use strict";

const crypto = require("crypto");
const term = require("./licenseTerm");

const DAY_MS = term.MS_PER_DAY;
const HOUR_MS = term.MS_PER_HOUR;
const UNLIMITED_DAYS = term.UNLIMITED_DAYS;
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
  return term.isUnlimited(data);
}

function formatRemaining(expiresAt, now) {
  const left = expiresAt - now;
  if (left <= 0) return "만료됨";
  if (left >= DAY_MS) return `${Math.ceil(left / DAY_MS)}일 남음`;
  if (left >= HOUR_MS) return `${Math.floor(left / HOUR_MS)}시간 남음`;
  return `${Math.max(1, Math.ceil(left / 60000))}분 남음`;
}

function getLicenseLifecycle(data, now = Date.now()) {
  const days = Number(data?.days || 0);
  const hours = Number(data?.hours || 0);
  const unlimited = isUnlimited(data);
  const disabled = data?.disabled === true;
  const firstVerify = isFirstVerifyLicense(data);
  const activatedAt = toMillis(data?.activatedAt);
  const createdAt = toMillis(data?.createdAt);
  const pending = firstVerify && activatedAt <= 0;
  const durationMs = term.durationMs(data);

  let expiresAt = 0;
  if (!unlimited && !pending) {
    expiresAt = toMillis(data?.expiresAt);
    if (expiresAt <= 0) {
      const base = firstVerify ? activatedAt : createdAt;
      expiresAt = base > 0 && durationMs > 0 ? base + durationMs : 0;
    }
  }

  const expired = !unlimited && !pending && (expiresAt <= 0 || now >= expiresAt);
  let status = "active";
  if (disabled) status = "disabled";
  else if (pending) status = "pending";
  else if (expired) status = "expired";

  const remainingMs = unlimited || pending ? null : Math.max(0, expiresAt - now);
  const remainingDays = remainingMs === null ? null : Math.ceil(remainingMs / DAY_MS);
  const baseForSignature = activatedAt || createdAt || toMillis(data?.issuedAt) || now;
  const signatureExpiresAt = unlimited
    ? Math.max(baseForSignature + durationMs, now + DAY_MS)
    : expiresAt;

  return {
    status,
    disabled,
    pending,
    expired,
    unlimited,
    days,
    hours,
    term: term.termText(data),
    durationMs,
    issuedAt: toMillis(data?.issuedAt) || createdAt,
    activatedAt: pending ? 0 : activatedAt || createdAt,
    expiresAt: unlimited || pending ? 0 : expiresAt,
    signatureExpiresAt: pending ? 0 : signatureExpiresAt,
    remainingMs,
    remainingDays,
    remainingText: unlimited
      ? "무제한"
      : pending
        ? `${term.termText(data)} · 첫 인증 전`
        : formatRemaining(expiresAt, now),
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
  days = 0,
  hours = 0,
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
    hours,
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
  const expiresAt = lifecycle.unlimited
    ? null
    : timestampFromMillis(now + lifecycle.durationMs);
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
  HOUR_MS,
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
