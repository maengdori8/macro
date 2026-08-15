"use strict";

const assert = require("node:assert/strict");
const test = require("node:test");
const { DAY_MS, FIRST_VERIFY_POLICY } = require("../lib/license_lifecycle");
const { verifyAndActivateLicense } = require("../lib/license_service");
const { FakeFirestore } = require("./fake_firestore");

const KEY = "ABCDE-FGHJK-LMNPQ-RSTUV-WXYZ2";
const HWID = "0123456789abcdef0123456789abcdef";

function pendingLicense(issuedAt) {
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
  };
}

test("동시 첫 인증도 시작 시각을 한 번만 기록한다", async () => {
  const issuedAt = Date.UTC(2026, 0, 1);
  const firstNow = Date.UTC(2026, 6, 25, 12);
  const db = new FakeFirestore({ [`licenses/${KEY}`]: pendingLicense(issuedAt) });
  const requests = Array.from({ length: 20 }, (_, index) =>
    verifyAndActivateLicense({
      db,
      key: KEY,
      hwid: HWID,
      now: firstNow + index * 1000,
      timestampFromMillis: (value) => value,
    })
  );
  const results = await Promise.all(requests);
  assert.equal(results.every((result) => result.valid), true);
  const stored = db.read(`licenses/${KEY}`);
  assert.equal(stored.activatedAt, firstNow);
  assert.equal(stored.createdAt, firstNow);
  assert.equal(stored.expiresAt, firstNow + 30 * DAY_MS);
  assert.deepEqual(stored.hwids, [HWID]);
});

test("재인증은 만료 시각을 다시 시작하지 않는다", async () => {
  const now = Date.UTC(2026, 6, 25);
  const db = new FakeFirestore({ [`licenses/${KEY}`]: pendingLicense(now - 10 * DAY_MS) });
  await verifyAndActivateLicense({
    db, key: KEY, hwid: HWID, now, timestampFromMillis: (value) => value,
  });
  await verifyAndActivateLicense({
    db, key: KEY, hwid: HWID, now: now + 10 * DAY_MS, timestampFromMillis: (value) => value,
  });
  assert.equal(db.read(`licenses/${KEY}`).expiresAt, now + 30 * DAY_MS);
});

test("3시간권은 첫 인증 후 정확히 3시간이며 서명 만료값도 동일하다", async () => {
  const now = Date.UTC(2026, 7, 15, 12);
  const db = new FakeFirestore({
    [`licenses/${KEY}`]: { ...pendingLicense(now - 100000), days:0, hours:3 },
  });
  const result = await verifyAndActivateLicense({
    db, key:KEY, hwid:HWID, now, timestampFromMillis:(value) => value,
  });
  assert.equal(result.valid, true);
  assert.equal(result.term, "3시간권");
  assert.equal(result.expiresAt, now + 3 * 60 * 60 * 1000);
  assert.equal(result.signatureExpiresAt, result.expiresAt);
  assert.match(result.message, /3시간권/);
});

test("기기 한도를 넘긴 인증은 거부하고 활성 시각을 변경하지 않는다", async () => {
  const now = Date.UTC(2026, 6, 25);
  const data = {
    ...pendingLicense(now),
    activatedAt: now,
    createdAt: now,
    expiresAt: now + 30 * DAY_MS,
    hwids: ["aaaaaaaa", "bbbbbbbb", "cccccccc"],
  };
  const db = new FakeFirestore({ [`licenses/${KEY}`]: data });
  const result = await verifyAndActivateLicense({
    db,
    key: KEY,
    hwid: "dddddddd",
    now: now + DAY_MS,
    timestampFromMillis: (value) => value,
  });
  assert.equal(result.valid, false);
  assert.match(result.message, /기기 등록 한도/);
  assert.equal(db.read(`licenses/${KEY}`).activatedAt, now);
});
