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

module.exports = async function handler(req, res) {
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
      });
    } catch (err) {
      console.error("Version check error:", err);
      return res.status(500).json({ version: "1.0.0", url: "", changelog: "" });
    }
  }

  if (req.method === "POST") {
    const authKey = req.headers["x-admin-key"];
    if (!ADMIN_KEY || authKey !== ADMIN_KEY) {
      return res.status(401).json({ success: false, message: "인증 실패" });
    }

    const { version, url, changelog } = req.body || {};
    if (!version || typeof version !== "string") {
      return res.status(400).json({ success: false, message: "version이 필요합니다." });
    }

    try {
      await db.collection("config").doc("version").set({
        version,
        url: url || "",
        changelog: changelog || "",
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
