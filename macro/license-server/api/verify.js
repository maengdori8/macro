"use strict";

const admin = require("firebase-admin");
const sec = require("../lib/security");
const { sendSignedVerdict } = require("../lib/licenseSign");
const { verifyAndActivateLicense } = require("../lib/license_service");
const { isValidProduct, normalizeProduct } = require("../lib/product");
const {
  autoExitRatioAppliesTo,
  resolveExitSettings,
  toClientPayload,
} = require("../lib/pro_settings");

if (!admin.apps.length) {
  admin.initializeApp({
    credential: admin.credential.cert({
      projectId: process.env.FIREBASE_PROJECT_ID,
      clientEmail: process.env.FIREBASE_CLIENT_EMAIL,
      privateKey: (process.env.FIREBASE_PRIVATE_KEY || "").replace(/\\n/g, "\n"),
    }),
  });
}

const db = admin.firestore();
const timestampFromMillis = (value) => admin.firestore.Timestamp.fromMillis(value);
const VERIFY_DB_TIMEOUT_MS = 5000;

module.exports = async function handler(req, res) {
  sec.setSecurityHeaders(res);
  sec.applyCors(req, res);
  if (req.method === "OPTIONS") return res.status(204).end();
  if (req.method !== "POST") {
    return res.status(405).json({ valid: false, message: "Method Not Allowed" });
  }

  const { key, hwid, nonce } = req.body || {};
  if (!sec.isValidKeyFormat(key)) {
    return res.status(400).json({ valid: false, message: "키 형식이 올바르지 않습니다." });
  }
  if (!sec.isValidHwidFormat(hwid)) {
    return res.status(400).json({ valid: false, message: "기기 정보가 올바르지 않습니다." });
  }
  // nonce는 매 요청 새로 생성된 hex. 서명에 묶어 재전송을 막는다.
  if (typeof nonce !== "string" || !/^[0-9a-f]{8,64}$/.test(nonce)) {
    return res.status(400).json({ valid: false, message: "요청 형식이 올바르지 않습니다." });
  }
  // product가 없으면 구버전 mAuto 클라이언트다 → v1 서명 + product "macro"로 취급.
  // 있으면 v2 서명에 묶어, 다른 제품 키로의 교차 사용을 차단한다.
  const product = normalizeProduct(req.body && req.body.product);
  if (product && !isValidProduct(product)) {
    return res.status(400).json({ valid: false, message: "요청 형식이 올바르지 않습니다." });
  }

  const ip = sec.getClientIp(req);
  const rl = await sec.rateLimit(db, { bucket: "verify", ip, max: 20, windowMs: 60000 });
  if (!rl.allowed) {
    return res.status(429).json({
      valid: false,
      message: "요청이 너무 많습니다. 잠시 후 다시 시도하세요.",
    });
  }

  const denied = (message) =>
    sendSignedVerdict(res, { verdict: "invalid", hwid, nonce, exp: 0, message, product });

  try {
    // Firestore가 할당량 소진 등으로 멈추면 무한 재시도로 함수가 300초 행이 된다
    // (실제 장애 원인). 타임아웃으로 감싸 빠르게 실패시키고, 실패는 서명된 거부로
    // 돌려보내 클라이언트가 fail-closed로 처리하게 한다.
    const result = await sec.withTimeout(
      verifyAndActivateLicense({
        db,
        key,
        hwid,
        product,
        timestampFromMillis,
      }),
      VERIFY_DB_TIMEOUT_MS,
      "license verification"
    );
    if (!result.valid) return denied(result.message);
    // 프로 문서면 운영 설정(2점차 자동 종료 비율)을 실어 보낸다. 서명 밖 참고값이라
    // 읽기 실패가 인증을 막으면 안 된다 — 필드가 없으면 클라이언트가 내장 기본값
    // (40%)으로 동작한다(fail-safe). 판정 기준은 요청이 아니라 **문서의 제품**이다.
    // 값은 키별(라이센스 문서) → 전역(config/pro_settings) → 기본값 순으로 정해진다.
    // auto_exit_ratio 는 구버전 클라이언트용(비율만), auto_exit_settings 는 규칙 전부.
    let extra;
    if (autoExitRatioAppliesTo(result.product)) {
      try {
        const resolved = await sec.withTimeout(
          resolveExitSettings(db, result.exitSettings),
          2000,
          "pro settings read"
        );
        extra = {
          auto_exit_ratio: resolved.autoExitRatio,
          auto_exit_settings: toClientPayload(resolved),
        };
      } catch (err) {
        console.error("Pro settings read error:", err);
      }
    }
    return sendSignedVerdict(res, {
      verdict: "valid",
      hwid,
      nonce,
      exp: Math.floor(result.signatureExpiresAt / 1000),
      message: result.message,
      // ⚠️ v1/v2 는 **요청에 product 가 있었는지**로 갈린다(구버전 클라이언트 보호).
      // v2 로 서명할 때는 요청한 제품이 아니라 **문서의 제품**을 넣는다 — 그래야
      // 프로 키로 일반 요청을 만족시킨 경우 클라이언트가 티어를 읽을 수 있고,
      // 그 티어가 서명에 묶여 위조가 불가능해진다.
      product: product ? result.product || product : "",
    }, extra);
  } catch (err) {
    console.error("License verify error:", err);
    if (sec.isTransientStoreError(err)) {
      res.setHeader("Retry-After", "5");
      return res.status(503).json({
        valid: false,
        retryable: true,
        message: "인증 서버가 일시적으로 혼잡합니다. 잠시 후 다시 시도하세요.",
      });
    }
    return res.status(500).json({ valid: false, message: "서버 오류가 발생했습니다." });
  }
};
