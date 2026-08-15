"use strict";

const assert = require("node:assert/strict");
const test = require("node:test");
const {
  DAY_MS,
  FIRST_VERIFY_POLICY,
  buildFirstActivationUpdate,
  buildUnusedMigrationUpdate,
  createSecureLicenseKey,
  getLicenseLifecycle,
  isCanonicalLicenseKey,
  isMigratableUnusedLicense,
} = require("../lib/license_lifecycle");

test("첫 인증 전 키는 발급 후 시간이 지나도 만료되지 않는다", () => {
  const issuedAt = Date.UTC(2026, 0, 1);
  const data = {
    days: 30,
    createdAt: issuedAt,
    issuedAt,
    activationPolicy: FIRST_VERIFY_POLICY,
    activatedAt: null,
    expiresAt: null,
    disabled: false,
    hwids: [],
  };
  const lifecycle = getLicenseLifecycle(data, issuedAt + 365 * DAY_MS);
  assert.equal(lifecycle.status, "pending");
  assert.equal(lifecycle.expired, false);
  assert.equal(lifecycle.expiresAt, 0);
});

test("첫 인증 업데이트는 인증 시각부터 정확히 이용기간을 계산한다", () => {
  const now = Date.UTC(2026, 6, 25, 12);
  const data = {
    days: 30,
    createdAt: now - 90 * DAY_MS,
    activationPolicy: FIRST_VERIFY_POLICY,
    activatedAt: null,
  };
  const update = buildFirstActivationUpdate(data, now, (value) => value);
  assert.equal(update.activatedAt, now);
  assert.equal(update.createdAt, now);
  assert.equal(update.expiresAt, now + 30 * DAY_MS);
  assert.deepEqual(buildFirstActivationUpdate({ ...data, ...update }, now + DAY_MS, (value) => value), {});
});

test("기존 사용 중 키는 기존 createdAt 기준 만료일을 유지한다", () => {
  const createdAt = Date.UTC(2026, 6, 1);
  const lifecycle = getLicenseLifecycle(
    { days: 30, createdAt, hwids: ["abc"], disabled: false },
    createdAt + 10 * DAY_MS
  );
  assert.equal(lifecycle.status, "active");
  assert.equal(lifecycle.expiresAt, createdAt + 30 * DAY_MS);
  assert.equal(lifecycle.remainingDays, 20);
});

test("만료 시각이 되는 즉시 키를 만료 처리한다", () => {
  const createdAt = Date.UTC(2026, 6, 1);
  const expiresAt = createdAt + 30 * DAY_MS;
  const lifecycle = getLicenseLifecycle(
    { days: 30, createdAt, hwids: ["abc"], disabled: false },
    expiresAt
  );
  assert.equal(lifecycle.status, "expired");
  assert.equal(lifecycle.remainingDays, 0);
});

test("HWID가 없거나 빈 기존 키만 마이그레이션 대상이다", () => {
  assert.equal(isMigratableUnusedLicense({ days: 30, createdAt: 1 }), true);
  assert.equal(isMigratableUnusedLicense({ days: 30, createdAt: 1, hwids: [] }), true);
  assert.equal(isMigratableUnusedLicense({ days: 30, createdAt: 1, hwids: ["used"] }), false);
  assert.equal(isMigratableUnusedLicense({ days: 30, createdAt: 1, hwids: "unknown" }), false);
  assert.equal(isMigratableUnusedLicense({ days: 30, activationPolicy: FIRST_VERIFY_POLICY, hwids: [] }), false);
});

test("미사용 마이그레이션은 기존 발급 시각을 보존하고 만료를 대기시킨다", () => {
  const createdAt = Date.UTC(2025, 0, 1);
  const now = Date.UTC(2026, 6, 25);
  const update = buildUnusedMigrationUpdate({ createdAt, hwids: [] }, now, (value) => value);
  assert.equal(update.issuedAt, createdAt);
  assert.equal(update.activationPolicy, FIRST_VERIFY_POLICY);
  assert.equal(update.activatedAt, null);
  assert.equal(update.expiresAt, null);
  assert.equal(getLicenseLifecycle({ days: 30, createdAt, hwids: [], ...update }, now).status, "pending");
});

test("암호학적 키 10000개는 형식이 맞고 중복이 없다", () => {
  const keys = new Set();
  for (let index = 0; index < 10000; index += 1) {
    const key = createSecureLicenseKey();
    assert.equal(isCanonicalLicenseKey(key), true);
    keys.add(key);
  }
  assert.equal(keys.size, 10000);
});
