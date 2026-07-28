// 공용 보안 유틸리티: CORS, Rate Limiting, 입력 검증, 보안 헤더

const RATE_LIMIT_MAX_ENTRIES = 10000;
const RATE_LIMIT_STATE_KEY = "__mAutoLicenseRateLimitsV2";
const rateLimitState =
  globalThis[RATE_LIMIT_STATE_KEY] ||
  (globalThis[RATE_LIMIT_STATE_KEY] = new Map());

// ─── 클라이언트 IP 추출 ───
function getClientIp(req) {
  const xff = req.headers["x-forwarded-for"];
  if (xff) return String(xff).split(",")[0].trim();
  const xri = req.headers["x-real-ip"];
  if (xri) return String(xri).trim();
  return (req.socket && req.socket.remoteAddress) || "unknown";
}

// ─── 보안 헤더 ───
function setSecurityHeaders(res) {
  res.setHeader("X-Content-Type-Options", "nosniff");
  res.setHeader("X-Frame-Options", "DENY");
  res.setHeader("Referrer-Policy", "no-referrer");
  res.setHeader("Strict-Transport-Security", "max-age=63072000; includeSubDomains; preload");
  res.setHeader("Permissions-Policy", "geolocation=(), microphone=(), camera=()");
}

// ─── CORS ───
// 데스크톱 앱(urllib)은 Origin 헤더가 없음 → 통과
// 브라우저(admin 패널)는 동일 출처만 허용
function applyCors(req, res, allowedOrigins) {
  const origin = req.headers.origin;
  res.setHeader("Vary", "Origin");
  res.setHeader("Access-Control-Allow-Methods", "GET,POST,PATCH,DELETE,OPTIONS");
  res.setHeader("Access-Control-Allow-Headers", "Content-Type, X-Admin-Key");
  res.setHeader("Access-Control-Max-Age", "86400");

  if (!origin) return true; // 브라우저가 아닌 클라이언트(앱)
  if (allowedOrigins && allowedOrigins.length > 0) {
    let sameOrigin = false;
    try {
      const requestHost = String(
        req.headers["x-forwarded-host"] || req.headers.host || ""
      ).toLowerCase();
      sameOrigin = requestHost !== "" && new URL(origin).host.toLowerCase() === requestHost;
    } catch {
      sameOrigin = false;
    }
    if (sameOrigin || allowedOrigins.includes(origin)) {
      res.setHeader("Access-Control-Allow-Origin", origin);
      return true;
    }
    // 허용되지 않은 Origin
    return false;
  }
  return true;
}

// ─── Rate Limiting (서버리스 인스턴스 메모리 기반, IP별) ───
// Firestore에 요청마다 기록하면 상태 보고만으로 무료 쓰기 할당량이 고갈된다.
// 인스턴스별 제한은 전역 분산 제한보다 느슨하지만, 입력 검증·관리자 키 검증과
// 함께 사용하면서 DB 쓰기를 0으로 유지하는 것이 라이선스 가용성에 더 안전하다.
async function rateLimit(
  _db,
  { bucket, ip, max, windowMs, now = Date.now() }
) {
  const safeBucket = String(bucket || "default").slice(0, 64);
  const safeIp = String(ip || "unknown").slice(0, 128);
  const key = `${safeBucket}:${safeIp}`;
  const previous = rateLimitState.get(key);
  let windowStart = Number(now);
  let count = 1;

  if (
    previous &&
    Number(now) - Number(previous.windowStart) < Number(windowMs)
  ) {
    windowStart = Number(previous.windowStart);
    count = Number(previous.count) + 1;
  }
  rateLimitState.delete(key);
  rateLimitState.set(key, { windowStart, count });

  if (rateLimitState.size > RATE_LIMIT_MAX_ENTRIES) {
    for (const [entryKey, entry] of rateLimitState) {
      if (
        Number(now) - Number(entry.windowStart) >= Number(windowMs) ||
        rateLimitState.size > RATE_LIMIT_MAX_ENTRIES
      ) {
        rateLimitState.delete(entryKey);
      }
      if (rateLimitState.size <= RATE_LIMIT_MAX_ENTRIES) break;
    }
  }
  return { allowed: count <= Number(max), count };
}

function withTimeout(promise, timeoutMs, label = "database operation") {
  const safeTimeout = Math.max(1, Number(timeoutMs) || 1);
  let timer;
  const timeout = new Promise((_, reject) => {
    timer = setTimeout(() => {
      const error = new Error(`${label} timed out`);
      error.code = "DB_TIMEOUT";
      reject(error);
    }, safeTimeout);
  });
  return Promise.race([Promise.resolve(promise), timeout]).finally(() => {
    clearTimeout(timer);
  });
}

function isTransientStoreError(error) {
  const code = String(error?.code || "").toLowerCase();
  return [
    "4",
    "8",
    "14",
    "deadline-exceeded",
    "resource-exhausted",
    "unavailable",
    "db_timeout",
  ].includes(code);
}

function resetRateLimitsForTests() {
  rateLimitState.clear();
}

// ─── 입력 검증 ───
// 라이센스 키: 영문/숫자/하이픈, 1~64자 (관리자가 임의 키도 만들 수 있어 느슨함)
function isValidKeyFormat(key) {
  return typeof key === "string" && /^[A-Za-z0-9\-]{1,64}$/.test(key);
}

// HWID: 32자 hex (get_hwid는 sha256[:32])
function isValidHwidFormat(hwid) {
  return typeof hwid === "string" && /^[a-f0-9]{8,64}$/i.test(hwid);
}

// 디스코드 ID: 숫자 17~20자 (스노우플레이크) 또는 빈 문자열
function isValidDiscordId(id) {
  return typeof id === "string" && (id === "" || /^\d{17,20}$/.test(id));
}

// 버전: x.y.z
function isValidVersion(v) {
  return typeof v === "string" && /^\d{1,4}(\.\d{1,4}){0,3}$/.test(v);
}

// URL: https만 허용
function isValidHttpsUrl(u) {
  if (typeof u !== "string" || u === "") return true; // 빈 값 허용
  try {
    const parsed = new URL(u);
    return parsed.protocol === "https:";
  } catch {
    return false;
  }
}

// 상수 시간 문자열 비교 (타이밍 공격 방지)
function timingSafeEqual(a, b) {
  const crypto = require("crypto");
  if (typeof a !== "string" || typeof b !== "string") return false;
  const ba = Buffer.from(a);
  const bb = Buffer.from(b);
  if (ba.length !== bb.length) return false;
  return crypto.timingSafeEqual(ba, bb);
}

// 관리자 키 검증
function verifyAdminKey(req) {
  const ADMIN_KEY = process.env.ADMIN_KEY;
  if (!ADMIN_KEY) return false;
  const provided = req.headers["x-admin-key"];
  return timingSafeEqual(String(provided || ""), ADMIN_KEY);
}

module.exports = {
  getClientIp,
  setSecurityHeaders,
  applyCors,
  rateLimit,
  withTimeout,
  isTransientStoreError,
  resetRateLimitsForTests,
  isValidKeyFormat,
  isValidHwidFormat,
  isValidDiscordId,
  isValidVersion,
  isValidHttpsUrl,
  timingSafeEqual,
  verifyAdminKey,
};
