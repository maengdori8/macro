"use strict";

const crypto = require("crypto");

const DEFAULT_STATUS_TTL_MS = 5 * 60 * 1000;
const STATUS_CACHE_MAX_ENTRIES = 5000;
const STATUS_CACHE_KEY = "__mAutoStatusThrottleV1";
const statusCache =
  globalThis[STATUS_CACHE_KEY] ||
  (globalThis[STATUS_CACHE_KEY] = new Map());

function cacheKey(key, hwid) {
  return crypto
    .createHash("sha256")
    .update(`${String(key)}\0${String(hwid)}`)
    .digest("hex");
}

function statusSignature({ rank, running, message }) {
  return JSON.stringify([
    rank === undefined ? null : rank,
    Boolean(running),
    String(message || ""),
  ]);
}

function shouldSkipStatus(
  payload,
  now = Date.now(),
  ttlMs = DEFAULT_STATUS_TTL_MS
) {
  const entry = statusCache.get(cacheKey(payload.key, payload.hwid));
  return Boolean(
    entry &&
      Number(now) - Number(entry.persistedAt) < Number(ttlMs) &&
      entry.signature === statusSignature(payload)
  );
}

function rememberStatus(payload, now = Date.now()) {
  const key = cacheKey(payload.key, payload.hwid);
  statusCache.delete(key);
  statusCache.set(key, {
    persistedAt: Number(now),
    signature: statusSignature(payload),
  });
  while (statusCache.size > STATUS_CACHE_MAX_ENTRIES) {
    const oldest = statusCache.keys().next().value;
    if (oldest === undefined) break;
    statusCache.delete(oldest);
  }
}

function resetStatusThrottleForTests() {
  statusCache.clear();
}

module.exports = {
  DEFAULT_STATUS_TTL_MS,
  shouldSkipStatus,
  rememberStatus,
  resetStatusThrottleForTests,
};
