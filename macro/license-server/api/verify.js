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
  if (req.method !== "POST") {
    return res.status(405).json({ valid: false, message: "Method Not Allowed" });
  }

  const { key } = req.body || {};

  if (!key || typeof key !== "string") {
    return res.status(400).json({ valid: false, message: "키가 제공되지 않았습니다." });
  }

  try {
    const doc = await db.collection("licenses").doc(key).get();

    if (!doc.exists) {
      return res.status(200).json({ valid: false, message: "존재하지 않는 라이센스 키입니다." });
    }

    const data = doc.data();

    if (data.disabled === true) {
      return res.status(200).json({ valid: false, message: "비활성화된 라이센스 키입니다." });
    }

    const now = Date.now();
    const createdAt = data.createdAt?.toMillis?.() || data.createdAt || 0;
    const days = data.days || 0;
    const expiresAt = createdAt + days * 86400000;

    if (now > expiresAt) {
      return res.status(200).json({ valid: false, message: "만료된 라이센스 키입니다." });
    }

    const remainingMs = expiresAt - now;
    const remainingDays = Math.ceil(remainingMs / 86400000);

    return res.status(200).json({
      valid: true,
      message: `유효한 라이센스입니다. (${days}일권, ${remainingDays}일 남음)`,
    });
  } catch (err) {
    console.error("License verify error:", err);
    return res.status(500).json({ valid: false, message: "서버 오류가 발생했습니다." });
  }
};
