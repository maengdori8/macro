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

// 봇 전용: N일 이내 만료 예정이고 디스코드가 연결된 라이센스 목록(DM 알림용).
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

  let days = parseInt(req.query.days, 10);
  if (!Number.isInteger(days) || days < 1 || days > 30) days = 3;

  try {
    const snap = await db.collection("licenses").where("discordId", "!=", "").get();
    const now = Date.now();
    const horizon = now + days * 86400000;
    const list = [];
    snap.forEach((doc) => {
      const d = doc.data();
      if (d.disabled === true) return;
      if (term.isUnlimited(d)) return; // 무제한 제외
      const expiresAt = term.expiresAt(d);
      if (expiresAt > now && expiresAt <= horizon) {
        list.push({
          discordId: d.discordId,
          remainingDays: Math.max(0, Math.ceil((expiresAt - now) / 86400000)),
        });
      }
    });
    return res.status(200).json({ success: true, count: list.length, list });
  } catch (err) {
    console.error("expiring error:", err);
    return res.status(500).json({ success: false, message: "서버 오류가 발생했습니다." });
  }
};
