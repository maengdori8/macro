const admin = require("firebase-admin");
const sec = require("../lib/security");
const term = require("../lib/licenseTerm");

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

// 봇 전용: discordId로 라이센스 요약 정보를 반환(만료일/남은기간/기기 수).
module.exports = async function handler(req, res) {
  sec.setSecurityHeaders(res);
  sec.applyCors(req, res);
  if (req.method === "OPTIONS") return res.status(204).end();
  if (req.method !== "GET") {
    return res.status(405).json({ success: false, message: "Method Not Allowed" });
  }
  if (!BOT_API_KEY || !sec.timingSafeEqual(String(req.headers["x-bot-key"] || ""), BOT_API_KEY)) {
    return res.status(401).json({ success: false, message: "인증 실패" });
  }

  const ip = sec.getClientIp(req);
  const rl = await sec.rateLimit(db, { bucket: "my_license", ip, max: 120, windowMs: 60000 });
  if (!rl.allowed) {
    return res.status(429).json({ success: false, message: "요청이 너무 많습니다." });
  }

  const discordId = req.query.discordId;
  if (!sec.isValidDiscordId(discordId) || discordId === "") {
    return res.status(400).json({ success: false, message: "discordId가 올바르지 않습니다." });
  }

  try {
    const snap = await db.collection("licenses").where("discordId", "==", discordId).limit(1).get();
    if (snap.empty) {
      return res.status(200).json({ success: true, registered: false });
    }
    const d = snap.docs[0].data();
    const createdAt = d.createdAt?.toMillis?.() || d.createdAt || 0;
    const days = d.days || 0;
    const unlimited = term.isUnlimited(d);
    const expiresAt = term.expiresAt(d);
    const now = Date.now();
    const remainingDays = unlimited ? null : Math.max(0, Math.ceil((expiresAt - now) / 86400000));
    const remainingText = term.remainingText(d, now);

    return res.status(200).json({
      success: true,
      registered: true,
      disabled: d.disabled === true,
      expired: !unlimited && now > expiresAt,
      days,
      unlimited,
      expiresAt: unlimited ? null : expiresAt,
      remainingDays,
      hwidCount: (d.hwids || []).length,
      maxHwids: d.maxHwids || 3,
    });
  } catch (err) {
    console.error("my-license error:", err);
    return res.status(500).json({ success: false, message: "서버 오류가 발생했습니다." });
  }
};
