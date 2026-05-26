const admin = require("firebase-admin");

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

module.exports = async function handler(req, res) {
  // POST: 매크로에서 상태 업데이트
  if (req.method === "POST") {
    const { key, rank, running, message } = req.body || {};
    if (!key) {
      return res.status(400).json({ success: false, message: "key가 필요합니다." });
    }

    // 라이센스 존재 확인
    const licDoc = await db.collection("licenses").doc(key).get();
    if (!licDoc.exists) {
      return res.status(404).json({ success: false, message: "유효하지 않은 라이센스입니다." });
    }

    try {
      await db.collection("status").doc(key).set(
        {
          rank: rank !== undefined ? rank : null,
          running: !!running,
          message: message || "",
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

  // GET: 디스코드 봇에서 상태 조회 (디스코드 유저ID로 조회)
  if (req.method === "GET") {
    const discordId = req.query.discordId;
    const key = req.query.key;

    try {
      // 라이센스 키로 직접 조회
      if (key) {
        const doc = await db.collection("status").doc(key).get();
        if (!doc.exists) {
          return res.status(404).json({ success: false, message: "상태 정보가 없습니다." });
        }
        const data = doc.data();
        return res.status(200).json({
          success: true,
          rank: data.rank,
          running: data.running,
          message: data.message || "",
          updatedAt: data.updatedAt?.toMillis?.() || 0,
        });
      }

      // 디스코드 유저ID로 라이센스 찾기
      if (discordId) {
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
      }

      return res.status(400).json({ success: false, message: "discordId 또는 key가 필요합니다." });
    } catch (err) {
      console.error("Status get error:", err);
      return res.status(500).json({ success: false, message: "상태 조회 실패" });
    }
  }

  return res.status(405).json({ success: false, message: "Method Not Allowed" });
};
