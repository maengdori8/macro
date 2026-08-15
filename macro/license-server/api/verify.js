"use strict";

const admin = require("firebase-admin");
const sec = require("../lib/security");
const { verifyAndActivateLicense } = require("../lib/license_service");

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

module.exports = async function handler(req, res) {
  sec.setSecurityHeaders(res);
  sec.applyCors(req, res);
  if (req.method === "OPTIONS") return res.status(204).end();
  if (req.method !== "POST") {
    return res.status(405).json({ valid: false, message: "Method Not Allowed" });
  }

  const ip = sec.getClientIp(req);
  const rl = await sec.rateLimit(db, { bucket: "verify", ip, max: 20, windowMs: 60000 });
  if (!rl.allowed) {
    return res.status(429).json({
      valid: false,
      message: "요청이 너무 많습니다. 잠시 후 다시 시도하세요.",
    });
  }

  const { key, hwid } = req.body || {};
  if (!sec.isValidKeyFormat(key)) {
    return res.status(400).json({ valid: false, message: "키 형식이 올바르지 않습니다." });
  }
  if (!sec.isValidHwidFormat(hwid)) {
    return res.status(400).json({ valid: false, message: "기기 정보가 올바르지 않습니다." });
  }

  try {
    const result = await verifyAndActivateLicense({
      db,
      key,
      hwid,
      timestampFromMillis,
    });
    return res.status(200).json(result);
  } catch (err) {
    console.error("License verify error:", err);
    return res.status(500).json({ valid: false, message: "서버 오류가 발생했습니다." });
  }
};
