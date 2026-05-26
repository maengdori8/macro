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
const ADMIN_KEY = process.env.ADMIN_KEY;

function unauthorized(res) {
  return res.status(401).json({ success: false, message: "인증 실패" });
}

module.exports = async function handler(req, res) {
  const authKey = req.headers["x-admin-key"];
  if (!ADMIN_KEY || authKey !== ADMIN_KEY) {
    return unauthorized(res);
  }

  const col = db.collection("licenses");

  if (req.method === "GET") {
    try {
      const snapshot = await col.orderBy("createdAt", "desc").get();
      const licenses = [];
      snapshot.forEach((doc) => {
        const data = doc.data();
        const createdAt = data.createdAt?.toMillis?.() || data.createdAt || 0;
        const days = data.days || 0;
        const expiresAt = createdAt + days * 86400000;
        const now = Date.now();
        licenses.push({
          key: doc.id,
          days,
          createdAt,
          expiresAt,
          disabled: data.disabled || false,
          expired: now > expiresAt,
          memo: data.memo || "",
          hwids: data.hwids || [],
          maxHwids: data.maxHwids || 3,
          discordId: data.discordId || "",
        });
      });
      return res.status(200).json({ success: true, licenses });
    } catch (err) {
      console.error("Admin list error:", err);
      return res.status(500).json({ success: false, message: "목록 조회 실패" });
    }
  }

  if (req.method === "POST") {
    const { key, days, memo } = req.body || {};
    if (!key || typeof key !== "string") {
      return res.status(400).json({ success: false, message: "key가 필요합니다." });
    }
    if (![1, 7, 30, 99999].includes(days)) {
      return res.status(400).json({ success: false, message: "days는 1, 7, 30, 99999 중 하나여야 합니다." });
    }

    try {
      const existing = await col.doc(key).get();
      if (existing.exists) {
        return res.status(409).json({ success: false, message: "이미 존재하는 키입니다." });
      }

      await col.doc(key).set({
        days,
        createdAt: admin.firestore.FieldValue.serverTimestamp(),
        disabled: false,
        memo: memo || "",
      });
      return res.status(201).json({ success: true, message: `${days}일권 라이센스가 발급되었습니다.` });
    } catch (err) {
      console.error("Admin create error:", err);
      return res.status(500).json({ success: false, message: "발급 실패" });
    }
  }

  if (req.method === "DELETE") {
    const { key } = req.body || {};
    if (!key || typeof key !== "string") {
      return res.status(400).json({ success: false, message: "key가 필요합니다." });
    }

    try {
      const doc = await col.doc(key).get();
      if (!doc.exists) {
        return res.status(404).json({ success: false, message: "존재하지 않는 키입니다." });
      }
      await col.doc(key).delete();
      return res.status(200).json({ success: true, message: "라이센스가 삭제되었습니다." });
    } catch (err) {
      console.error("Admin delete error:", err);
      return res.status(500).json({ success: false, message: "삭제 실패" });
    }
  }

  if (req.method === "PATCH") {
    const { key, disabled, resetHwids } = req.body || {};
    if (!key || typeof key !== "string") {
      return res.status(400).json({ success: false, message: "key가 필요합니다." });
    }

    try {
      const doc = await col.doc(key).get();
      if (!doc.exists) {
        return res.status(404).json({ success: false, message: "존재하지 않는 키입니다." });
      }

      if (resetHwids) {
        await col.doc(key).update({ hwids: [] });
        return res.status(200).json({ success: true, message: "HWID가 초기화되었습니다." });
      }

      const { discordId } = req.body;
      if (discordId !== undefined) {
        await col.doc(key).update({ discordId: discordId || "" });
        return res.status(200).json({ success: true, message: "디스코드 ID가 업데이트되었습니다." });
      }

      await col.doc(key).update({ disabled: !!disabled });
      const action = disabled ? "비활성화" : "활성화";
      return res.status(200).json({ success: true, message: `라이센스가 ${action}되었습니다.` });
    } catch (err) {
      console.error("Admin update error:", err);
      return res.status(500).json({ success: false, message: "수정 실패" });
    }
  }

  return res.status(405).json({ success: false, message: "Method Not Allowed" });
};
