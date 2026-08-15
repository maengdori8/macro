const admin = require("firebase-admin");
const sec = require("../lib/security");
const { getLicenseLifecycle } = require("../lib/license_lifecycle");

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
const BOT_API_KEY = process.env.BOT_API_KEY;

// 봇이 "!인증 <키>" 처리 시 호출: 라이센스 유효성 확인 + discordId 연결.
// HWID 슬롯은 건드리지 않습니다(커뮤니티 역할 부여용이지 기기 등록이 아님).
module.exports = async function handler(req, res) {
  sec.setSecurityHeaders(res);
  sec.applyCors(req, res);
  if (req.method === "OPTIONS") return res.status(204).end();
  if (req.method !== "POST") {
    return res.status(405).json({ success: false, message: "Method Not Allowed" });
  }

  // 봇 전용: BOT_API_KEY 없으면 누구도 호출 불가.
  if (!BOT_API_KEY || !sec.timingSafeEqual(String(req.headers["x-bot-key"] || ""), BOT_API_KEY)) {
    return res.status(401).json({ success: false, message: "인증 실패" });
  }

  const ip = sec.getClientIp(req);
  const rl = await sec.rateLimit(db, { bucket: "discord_link", ip, max: 60, windowMs: 60000 });
  if (!rl.allowed) {
    return res.status(429).json({ success: false, message: "요청이 너무 많습니다." });
  }

  const { key, discordId } = req.body || {};
  if (!sec.isValidKeyFormat(key)) {
    return res.status(400).json({ success: false, message: "키 형식이 올바르지 않습니다." });
  }
  if (!discordId || !sec.isValidDiscordId(discordId)) {
    return res.status(400).json({ success: false, message: "디스코드 ID가 올바르지 않습니다." });
  }

  try {
    const ref = db.collection("licenses").doc(key);
    const doc = await ref.get();
    if (!doc.exists) {
      return res.status(200).json({ success: false, message: "존재하지 않는 라이센스 키입니다." });
    }
    const data = doc.data();
    if (data.disabled === true) {
      return res.status(200).json({ success: false, message: "비활성화된 라이센스입니다." });
    }

    const now = Date.now();
    const lifecycle = getLicenseLifecycle(data, now);
    if (lifecycle.expired) {
      return res.status(200).json({ success: false, message: "만료된 라이센스입니다." });
    }

    // 1키 ↔ 1디스코드: 이미 다른 계정에 연결돼 있으면 거부(키 공유 차단).
    const existing = data.discordId || "";
    if (existing && existing !== discordId) {
      return res.status(200).json({
        success: false,
        message: "이미 다른 디스코드 계정에 등록된 키입니다. 관리자에게 문의하세요.",
      });
    }

    const alreadyLinked = existing === discordId;
    if (!alreadyLinked) {
      await ref.update({ discordId });
    }

    return res.status(200).json({
      success: true,
      message: alreadyLinked ? "이미 등록됨" : "인증 성공",
      alreadyLinked,
      days: lifecycle.days,
      unlimited: lifecycle.unlimited,
      pending: lifecycle.pending,
      remainingDays: lifecycle.unlimited
        ? null
        : lifecycle.pending
          ? lifecycle.days
          : lifecycle.remainingDays,
      expiresAt: lifecycle.expiresAt || null,
    });
  } catch (err) {
    console.error("discord-link error:", err);
    return res.status(500).json({ success: false, message: "서버 오류가 발생했습니다." });
  }
};
