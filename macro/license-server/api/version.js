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

const ALLOWED_ORIGINS = [
  "https://license-server-flame-eta.vercel.app",
  ...(process.env.ALLOWED_ORIGINS ? process.env.ALLOWED_ORIGINS.split(",") : []),
];

module.exports = async function handler(req, res) {
  sec.setSecurityHeaders(res);
  const corsOk = sec.applyCors(req, res, ALLOWED_ORIGINS);
  if (req.method === "OPTIONS") return res.status(204).end();

  if (req.method === "GET") {
    try {
      const doc = await db.collection("config").doc("version").get();
      if (!doc.exists) {
        return res.status(200).json({ version: "1.0.0", url: "", changelog: "" });
      }
      const data = doc.data();
      return res.status(200).json({
        version: data.version || "1.0.0",
        url: data.url || "",
        changelog: data.changelog || "",
        sha256: data.sha256 || "",
      });
    } catch (err) {
      console.error("Version check error:", err);
      return res.status(500).json({ version: "1.0.0", url: "", changelog: "" });
    }
  }

  if (req.method === "POST") {
    if (!corsOk) {
      return res.status(403).json({ success: false, message: "허용되지 않은 출처입니다." });
    }
    const ip = sec.getClientIp(req);
    const rl = await sec.rateLimit(db, { bucket: "version", ip, max: 10, windowMs: 60000, failClosed: true });
    if (!rl.allowed) {
      return res.status(429).json({ success: false, message: "요청이 너무 많습니다." });
    }
    if (!sec.verifyAdminKey(req)) {
      return res.status(401).json({ success: false, message: "인증 실패" });
    }

    const { version, url, changelog, sha256 } = req.body || {};
    if (!sec.isValidVersion(version)) {
      return res.status(400).json({ success: false, message: "버전 형식이 올바르지 않습니다. (예: 1.0.1)" });
    }
    if (!sec.isValidHttpsUrl(url)) {
      return res.status(400).json({ success: false, message: "다운로드 URL은 https여야 합니다." });
    }
    if (changelog !== undefined && (typeof changelog !== "string" || changelog.length > 1000)) {
      return res.status(400).json({ success: false, message: "변경사항은 1000자 이하여야 합니다." });
    }
    // SHA256: 빈 값 허용(미설정), 값이 있으면 64자리 hex여야 함.
    const cleanSha = typeof sha256 === "string" ? sha256.trim().toLowerCase() : "";
    if (cleanSha && !/^[a-f0-9]{64}$/.test(cleanSha)) {
      return res.status(400).json({ success: false, message: "SHA256은 64자리 hex여야 합니다." });
    }

    try {
      await db.collection("config").doc("version").set({
        version,
        url: url || "",
        changelog: changelog || "",
        sha256: cleanSha,
        updatedAt: admin.firestore.FieldValue.serverTimestamp(),
      });
      return res.status(200).json({ success: true, message: `버전 ${version}으로 업데이트되었습니다.` });
    } catch (err) {
      console.error("Version update error:", err);
      return res.status(500).json({ success: false, message: "버전 업데이트 실패" });
    }
  }

  return res.status(405).json({ success: false, message: "Method Not Allowed" });
};
