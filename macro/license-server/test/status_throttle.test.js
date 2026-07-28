"use strict";

const assert = require("node:assert/strict");
const test = require("node:test");
const {
  DEFAULT_STATUS_TTL_MS,
  shouldSkipStatus,
  rememberStatus,
  shouldPersistStatusDocument,
  resetStatusThrottleForTests,
} = require("../lib/status_throttle");

const BASE = {
  key: "ABCDE-FGHJK-LMNPQ-RSTUV-WXYZ2",
  hwid: "0123456789abcdef0123456789abcdef",
  rank: 11056,
  running: true,
  message: "running",
};

test("unchanged status skips database work during the warm TTL", () => {
  resetStatusThrottleForTests();
  assert.equal(shouldSkipStatus(BASE, 1000), false);
  rememberStatus(BASE, 1000);
  assert.equal(shouldSkipStatus(BASE, 1000 + 30000), true);
  assert.equal(
    shouldSkipStatus(
      { ...BASE, rank: BASE.rank + 1 },
      1000 + 30000
    ),
    false
  );
  assert.equal(
    shouldSkipStatus(BASE, 1000 + DEFAULT_STATUS_TTL_MS),
    false
  );
});

test("thirty-second heartbeats persist at most 288 times per day", () => {
  resetStatusThrottleForTests();
  let writes = 0;
  for (let now = 0; now < 24 * 60 * 60 * 1000; now += 30000) {
    if (shouldSkipStatus(BASE, now)) continue;
    writes += 1;
    rememberStatus(BASE, now);
  }
  assert.equal(writes, 288);
});

test("stop and message changes bypass the unchanged throttle", () => {
  resetStatusThrottleForTests();
  rememberStatus(BASE, 1000);
  assert.equal(
    shouldSkipStatus({ ...BASE, running: false }, 2000),
    false
  );
  assert.equal(
    shouldSkipStatus({ ...BASE, message: "stopped" }, 2000),
    false
  );
});

test("shared Firestore state limits heartbeats across serverless instances", () => {
  let current = null;
  let writes = 0;
  for (
    let now = 1000;
    now < 1000 + 24 * 60 * 60 * 1000;
    now += 30000
  ) {
    if (!shouldPersistStatusDocument(current, BASE, now)) continue;
    writes += 1;
    current = {
      rank: BASE.rank,
      running: BASE.running,
      message: BASE.message,
      updatedAt: now,
    };
  }
  assert.equal(writes, 288);
});

test("shared status writes immediately when rank or running changes", () => {
  const current = {
    rank: BASE.rank,
    running: BASE.running,
    message: BASE.message,
    updatedAt: 1000,
  };
  assert.equal(
    shouldPersistStatusDocument(current, BASE, 2000),
    false
  );
  assert.equal(
    shouldPersistStatusDocument(
      current,
      { ...BASE, rank: BASE.rank + 1 },
      2000
    ),
    true
  );
  assert.equal(
    shouldPersistStatusDocument(
      current,
      { ...BASE, running: false },
      2000
    ),
    true
  );
});
