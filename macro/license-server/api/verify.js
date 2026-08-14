const admin = require("firebase-admin");
const sec = require("../lib/security");
const { sendSignedVerdict } = require("../lib/licenseSign");

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
const MAX_HWIDS = 3;

module.exports = async function handler(req, res) {
  sec.setSecurityHeaders(res);
  sec.applyCors(req, res);

  if (req.method === "OPTIONS") return res.status(204).end();
  if (req.method !== "POST") {
    return res.status(405).json({ valid: false, message: "Method Not Allowed" });
  }

  // Rate limit: IP당 분당 20회
  const ip = sec.getClientIp(req);
  const rl = await sec.rateLimit(db, { bucket: "verify", ip, max: 20, windowMs: 60000 });
  if (!rl.allowed) {
    return res.status(429).json({ valid: false, message: "요청이 너무 많습니다. 잠시 후 다시 시도하세요." });
  }

  const { key, hwid, nonce } = req.body || {};

  if (!sec.isValidKeyFormat(key)) {
    return res.status(400).json({ valid: false, message: "키 형식이 올바르지 않습니다." });
  }
  if (!sec.isValidHwidFormat(hwid)) {
    return res.status(400).json({ valid: false, message: "기기 정보가 올바르지 않습니다." });
  }
  // nonce: 클라이언트가 매 요청 새로 만든 hex. 서명에 그대로 묶어 재전송을 막는다.
  // 형식이 어긋나면 서명해도 클라이언트가 자기 nonce와 대조해 거부하므로 여기서 먼저 튕긴다.
  if (typeof nonce !== "string" || !/^[0-9a-f]{8,64}$/.test(nonce)) {
    return res.status(400).json({ valid: false, message: "요청 형식이 올바르지 않습니다." });
  }

  // 판정 응답은 전부 서명 경로로 내보낸다(정상·거부 모두). verdict가 서명에 묶여
  // 가짜 서버의 위조와 MITM의 판정 뒤집기를 함께 차단한다.
  const denied = (message) =>
    sendSignedVerdict(res, { verdict: "invalid", hwid, nonce, exp: 0, message });

  try {
    const docRef = db.collection("licenses").doc(key);
    const doc = await docRef.get();

    if (!doc.exists) {
      return denied("존재하지 않는 라이센스 키입니다.");
    }

    const data = doc.data();

    if (data.disabled === true) {
      return denied("비활성화된 라이센스 키입니다.");
    }

    const now = Date.now();
    const createdAt = data.createdAt?.toMillis?.() || data.createdAt || 0;
    const days = data.days || 0;
    const expiresAt = createdAt + days * 86400000;

    if (now > expiresAt) {
      return denied("만료된 라이센스 키입니다.");
    }

    const hwids = data.hwids || [];
    const maxHwids = data.maxHwids || MAX_HWIDS;

    if (!hwids.includes(hwid)) {
      if (hwids.length >= maxHwids) {
        return denied(`기기 등록 한도 초과 (${maxHwids}대). 관리자에게 초기화를 요청하세요.`);
      }
      // 동시성 안전: 트랜잭션으로 한도 재확인 후 등록
      await db.runTransaction(async (tx) => {
        const fresh = await tx.get(docRef);
        const fd = fresh.data() || {};
        const cur = fd.hwids || [];
        if (cur.includes(hwid)) return;
        if (cur.length >= (fd.maxHwids || MAX_HWIDS)) {
          throw new Error("HWID_LIMIT");
        }
        tx.update(docRef, { hwids: admin.firestore.FieldValue.arrayUnion(hwid) });
      }).catch((e) => {
        if (e.message === "HWID_LIMIT") {
          const err = new Error("HWID_LIMIT");
          err.code = "HWID_LIMIT";
          throw err;
        }
        throw e;
      });
    }

    const remainingMs = expiresAt - now;
    const remainingDays = Math.ceil(remainingMs / 86400000);

    // exp는 초 단위(클라이언트가 exp-now>0을 초로 판정). 이 값이 서명에 묶여
    // 만료일 조작(exp만 늘리기)이 불가능하다.
    return sendSignedVerdict(res, {
      verdict: "valid",
      hwid,
      nonce,
      exp: Math.floor(expiresAt / 1000),
      message: `유효한 라이센스입니다. (${days}일권, ${remainingDays}일 남음)`,
    });
  } catch (err) {
    if (err.code === "HWID_LIMIT") {
      return denied("기기 등록 한도 초과. 관리자에게 초기화를 요청하세요.");
    }
    console.error("License verify error:", err);
    return res.status(500).json({ valid: false, message: "서버 오류가 발생했습니다." });
  }
};
