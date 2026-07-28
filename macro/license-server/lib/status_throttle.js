"use strict";

const crypto = require("crypto");

const DEFAULT_STATUS_TTL_MS = 5 * 60 * 1000;
const HEARTBEAT_BUCKET_MS = 30 * 1000;
const HEARTBEAT_BUCKET_COUNT = 10;
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

function heartbeatPhase(key, hwid, bucketCount = HEARTBEAT_BUCKET_COUNT) {
  const digest = crypto
    .createHash("sha256")
    .update(`${String(key)}\0${String(hwid)}`)
    .digest();
  return digest.readUInt32BE(0) % Number(bucketCount);
}

function shouldProcessHeartbeat(
  payload,
  now = Date.now(),
  bucketMs = HEARTBEAT_BUCKET_MS,
  bucketCount = HEARTBEAT_BUCKET_COUNT
) {
  // A stop event may be the client's final report, so it must never be sampled.
  if (!payload.running) return true;
  const currentBucket =
    Math.floor(Number(now) / Number(bucketMs)) % Number(bucketCount);
  return currentBucket === heartbeatPhase(
    payload.key,
    payload.hwid,
    bucketCount
  );
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

function toMillis(value) {
  if (value === null || value === undefined) return 0;
  if (typeof value === "number") return Number.isFinite(value) ? value : 0;
  if (typeof value.toMillis === "function") return value.toMillis();
  if (value instanceof Date) return value.getTime();
  return 0;
}

function shouldPersistStatusDocument(
  current,
  payload,
  now = Date.now(),
  ttlMs = DEFAULT_STATUS_TTL_MS
) {
  if (!current) return true;
  const currentRank =
    current.rank === undefined ? null : current.rank;
  if (currentRank !== payload.rank) return true;
  if (Boolean(current.running) !== Boolean(payload.running)) return true;
  if (String(current.message || "") !== String(payload.message || "")) {
    return true;
  }
  const updatedAt = toMillis(current.updatedAt);
  return updatedAt <= 0 || Number(now) - updatedAt >= Number(ttlMs);
}

function resetStatusThrottleForTests() {
  statusCache.clear();
}

module.exports = {
  DEFAULT_STATUS_TTL_MS,
  HEARTBEAT_BUCKET_MS,
  HEARTBEAT_BUCKET_COUNT,
  shouldProcessHeartbeat,
  shouldSkipStatus,
  rememberStatus,
  shouldPersistStatusDocument,
  resetStatusThrottleForTests,
};
