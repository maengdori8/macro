// 공용 보안 유틸리티: CORS, Rate Limiting, 입력 검증, 보안 헤더
const admin = require("firebase-admin");

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
    if (allowedOrigins.includes(origin)) {
      res.setHeader("Access-Control-Allow-Origin", origin);
      return true;
    }
    // 허용되지 않은 Origin
    return false;
  }
  return true;
}

// ─── Rate Limiting (Firestore 기반, IP별) ───
// failClosed=true: 오류 시 차단(관리자용), false: 통과(일반 사용자)
async function rateLimit(db, { bucket, ip, max, windowMs, failClosed = false }) {
  const ref = db.collection("ratelimits").doc(`${bucket}_${ip}`);
  const now = Date.now();
  try {
    const result = await db.runTransaction(async (tx) => {
      const snap = await tx.get(ref);
      let count = 1;
      let windowStart = now;
      if (snap.exists) {
        const d = snap.data();
        if (now - (d.windowStart || 0) < windowMs) {
          count = (d.count || 0) + 1;
          windowStart = d.windowStart;
        }
      }
      tx.set(ref, {
        count,
        windowStart,
        expiresAt: windowStart + windowMs,
      });
      return { allowed: count <= max, count };
    });
    return result;
  } catch (e) {
    console.error("RateLimit error:", e);
    return { allowed: !failClosed, count: 0 };
  }
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
  if (!ADMIN_KEY || ADMIN_KEY.length < 16) return false; // 약한 키 거부
  const provided = req.headers["x-admin-key"];
  return timingSafeEqual(String(provided || ""), ADMIN_KEY);
}

module.exports = {
  getClientIp,
  setSecurityHeaders,
  applyCors,
  rateLimit,
  isValidKeyFormat,
  isValidHwidFormat,
  isValidDiscordId,
  isValidVersion,
  isValidHttpsUrl,
  timingSafeEqual,
  verifyAdminKey,
};
