"use strict";

const assert = require("node:assert/strict");
const test = require("node:test");
const {
  DEFAULT_STATUS_TTL_MS,
  HEARTBEAT_BUCKET_MS,
  HEARTBEAT_BUCKET_COUNT,
  shouldProcessHeartbeat,
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

test("stateless sampling selects one running heartbeat every five minutes", () => {
  let selected = 0;
  const oneDay = 24 * 60 * 60 * 1000;
  for (let now = 0; now < oneDay; now += HEARTBEAT_BUCKET_MS) {
    if (shouldProcessHeartbeat(BASE, now)) selected += 1;
  }
  assert.equal(HEARTBEAT_BUCKET_COUNT, 10);
  assert.equal(selected, 288);
});

test("sampling phase is stable per license and device", () => {
  const selectedBuckets = [];
  for (let bucket = 0; bucket < HEARTBEAT_BUCKET_COUNT; bucket += 1) {
    if (
      shouldProcessHeartbeat(
        BASE,
        bucket * HEARTBEAT_BUCKET_MS
      )
    ) {
      selectedBuckets.push(bucket);
    }
  }
  assert.equal(selectedBuckets.length, 1);
  assert.equal(
    shouldProcessHeartbeat(
      BASE,
      (selectedBuckets[0] + HEARTBEAT_BUCKET_COUNT) *
        HEARTBEAT_BUCKET_MS
    ),
    true
  );
});

test("stop reports always bypass heartbeat sampling", () => {
  const stopped = { ...BASE, running: false };
  for (let bucket = 0; bucket < HEARTBEAT_BUCKET_COUNT; bucket += 1) {
    assert.equal(
      shouldProcessHeartbeat(
        stopped,
        bucket * HEARTBEAT_BUCKET_MS
      ),
      true
    );
  }
});

test("a full day of routine status reports performs at most 576 reads", () => {
  let reads = 0;
  for (
    let now = 0;
    now < 24 * 60 * 60 * 1000;
    now += HEARTBEAT_BUCKET_MS
  ) {
    if (!shouldProcessHeartbeat(BASE, now)) continue;
    reads += 2; // license authorization + shared status document
  }
  assert.equal(reads, 576);
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
