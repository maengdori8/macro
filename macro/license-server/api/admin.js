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

// 허용된 관리자 패널 출처 (환경변수로 추가 가능)
const ALLOWED_ORIGINS = [
  "https://license-server-flame-eta.vercel.app",
  ...(process.env.ALLOWED_ORIGINS ? process.env.ALLOWED_ORIGINS.split(",") : []),
];

function unauthorized(res) {
  return res.status(401).json({ success: false, message: "인증 실패" });
}

module.exports = async function handler(req, res) {
  sec.setSecurityHeaders(res);
  const corsOk = sec.applyCors(req, res, ALLOWED_ORIGINS);
  if (req.method === "OPTIONS") return res.status(204).end();
  if (!corsOk) {
    return res.status(403).json({ success: false, message: "허용되지 않은 출처입니다." });
  }

  // 관리자 키 브루트포스 방지: IP당 분당 10회
  const ip = sec.getClientIp(req);
  const rl = await sec.rateLimit(db, {
    bucket: "admin",
    ip,
    max: 10,
    windowMs: 60000,
    failClosed: true,
  });
  if (!rl.allowed) {
    return res.status(429).json({ success: false, message: "요청이 너무 많습니다." });
  }

  if (!sec.verifyAdminKey(req)) {
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
    if (!sec.isValidKeyFormat(key)) {
      return res.status(400).json({ success: false, message: "키 형식이 올바르지 않습니다." });
    }
    // 기간(일): 1~36500 사이 정수 또는 무제한(99999).
    if (!Number.isInteger(days) || (days !== 99999 && (days < 1 || days > 36500))) {
      return res.status(400).json({ success: false, message: "기간(일)은 1~36500 사이 정수 또는 무제한(99999)이어야 합니다." });
    }
    if (memo !== undefined && (typeof memo !== "string" || memo.length > 200)) {
      return res.status(400).json({ success: false, message: "메모는 200자 이하 문자열이어야 합니다." });
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
    if (!sec.isValidKeyFormat(key)) {
      return res.status(400).json({ success: false, message: "키 형식이 올바르지 않습니다." });
    }

    try {
      const doc = await col.doc(key).get();
      if (!doc.exists) {
        return res.status(404).json({ success: false, message: "존재하지 않는 키입니다." });
      }
      await col.doc(key).delete();
      await db.collection("status").doc(key).delete().catch(() => {});
      return res.status(200).json({ success: true, message: "라이센스가 삭제되었습니다." });
    } catch (err) {
      console.error("Admin delete error:", err);
      return res.status(500).json({ success: false, message: "삭제 실패" });
    }
  }

  if (req.method === "PATCH") {
    const { key, disabled, resetHwids } = req.body || {};
    if (!sec.isValidKeyFormat(key)) {
      return res.status(400).json({ success: false, message: "키 형식이 올바르지 않습니다." });
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

      // 기간 연장: 현재 만료(또는 지금) 기준으로 N일 추가.
      const { extendDays } = req.body;
      if (extendDays !== undefined) {
        if (!Number.isInteger(extendDays) || extendDays < 1 || extendDays > 36500) {
          return res.status(400).json({ success: false, message: "연장 일수는 1~36500 사이 정수여야 합니다." });
        }
        const data = doc.data();
        const curDays = data.days || 0;
        if (curDays === 99999) {
          return res.status(400).json({ success: false, message: "이미 무제한 라이센스입니다." });
        }
        const createdAt = data.createdAt?.toMillis?.() || data.createdAt || 0;
        const currentExpiry = createdAt + curDays * 86400000;
        // 아직 유효하면 만료일에서, 이미 만료됐으면 지금부터 연장.
        const base = Math.max(Date.now(), currentExpiry);
        const newExpiry = base + extendDays * 86400000;
        const newDays = Math.max(1, Math.round((newExpiry - createdAt) / 86400000));
        await col.doc(key).update({ days: newDays });
        const remainDays = Math.ceil((newExpiry - Date.now()) / 86400000);
        return res.status(200).json({ success: true, message: `${extendDays}일 연장되었습니다. (남은 약 ${remainDays}일)` });
      }

      // 무제한으로 전환.
      const { makeUnlimited } = req.body;
      if (makeUnlimited) {
        await col.doc(key).update({ days: 99999 });
        return res.status(200).json({ success: true, message: "무제한으로 전환되었습니다." });
      }

      const { discordId } = req.body;
      if (discordId !== undefined) {
        if (!sec.isValidDiscordId(discordId)) {
          return res.status(400).json({ success: false, message: "디스코드 ID 형식이 올바르지 않습니다. (숫자 17~20자)" });
        }
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
