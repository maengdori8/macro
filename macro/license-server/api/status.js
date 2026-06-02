const admin = require("firebase-admin");
const sec = require("../lib/security");

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

// 디스코드 봇 조회용 키 (봇만 알고 있음)
const BOT_API_KEY = process.env.BOT_API_KEY;

module.exports = async function handler(req, res) {
  sec.setSecurityHeaders(res);
  sec.applyCors(req, res);
  if (req.method === "OPTIONS") return res.status(204).end();

  const ip = sec.getClientIp(req);

  // ─── POST: 매크로에서 상태 업데이트 ───
  if (req.method === "POST") {
    const rl = await sec.rateLimit(db, { bucket: "status_post", ip, max: 30, windowMs: 60000 });
    if (!rl.allowed) {
      return res.status(429).json({ success: false, message: "요청이 너무 많습니다." });
    }

    const { key, hwid, rank, running, message } = req.body || {};
    if (!sec.isValidKeyFormat(key)) {
      return res.status(400).json({ success: false, message: "키 형식이 올바르지 않습니다." });
    }
    if (!sec.isValidHwidFormat(hwid)) {
      return res.status(400).json({ success: false, message: "기기 정보가 올바르지 않습니다." });
    }
    // rank 검증: null 또는 정수
    if (rank !== undefined && rank !== null && (!Number.isInteger(rank) || rank < 0 || rank > 99999999)) {
      return res.status(400).json({ success: false, message: "등수 값이 올바르지 않습니다." });
    }
    const safeMessage = typeof message === "string" ? message.slice(0, 200) : "";

    try {
      // 라이센스 + HWID 검증: 등록된 기기만 상태 전송 가능
      const licDoc = await db.collection("licenses").doc(key).get();
      if (!licDoc.exists) {
        return res.status(404).json({ success: false, message: "유효하지 않은 라이센스입니다." });
      }
      const licData = licDoc.data();
      if (licData.disabled === true) {
        return res.status(403).json({ success: false, message: "비활성화된 라이센스입니다." });
      }
      const hwids = licData.hwids || [];
      if (!hwids.includes(hwid)) {
        return res.status(403).json({ success: false, message: "등록되지 않은 기기입니다." });
      }

      await db.collection("status").doc(key).set(
        {
          rank: rank !== undefined ? rank : null,
          running: !!running,
          message: safeMessage,
          updatedAt: admin.firestore.FieldValue.serverTimestamp(),
        },
        { merge: true }
      );
      return res.status(200).json({ success: true });
    } catch (err) {
      console.error("Status update error:", err);
      return res.status(500).json({ success: false, message: "상태 업데이트 실패" });
    }
  }

  // ─── GET: 디스코드 봇에서 상태 조회 ───
  if (req.method === "GET") {
    // 봇 전용 API 키 필수 (브라우저/외부에서 무단 조회 방지)
    if (!BOT_API_KEY || !sec.timingSafeEqual(String(req.headers["x-bot-key"] || ""), BOT_API_KEY)) {
      return res.status(401).json({ success: false, message: "인증 실패" });
    }

    const rl = await sec.rateLimit(db, { bucket: "status_get", ip, max: 60, windowMs: 60000 });
    if (!rl.allowed) {
      return res.status(429).json({ success: false, message: "요청이 너무 많습니다." });
    }

    const discordId = req.query.discordId;

    try {
      if (!sec.isValidDiscordId(discordId) || discordId === "") {
        return res.status(400).json({ success: false, message: "discordId가 올바르지 않습니다." });
      }

      const licSnapshot = await db
        .collection("licenses")
        .where("discordId", "==", discordId)
        .limit(1)
        .get();

      if (licSnapshot.empty) {
        return res.status(404).json({
          success: false,
          message: "등록된 디스코드 계정이 없습니다. 관리자에게 문의하세요.",
        });
      }

      const licKey = licSnapshot.docs[0].id;
      const doc = await db.collection("status").doc(licKey).get();
      if (!doc.exists) {
        return res.status(404).json({ success: false, message: "매크로 상태 정보가 없습니다." });
      }
      const data = doc.data();
      return res.status(200).json({
        success: true,
        rank: data.rank,
        running: data.running,
        message: data.message || "",
        updatedAt: data.updatedAt?.toMillis?.() || 0,
      });
    } catch (err) {
      console.error("Status get error:", err);
      return res.status(500).json({ success: false, message: "상태 조회 실패" });
    }
  }

  return res.status(405).json({ success: false, message: "Method Not Allowed" });
};
