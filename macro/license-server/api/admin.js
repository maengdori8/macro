"use strict";

const admin = require("firebase-admin");
const sec = require("../lib/security");
const {
  DAY_MS,
  UNLIMITED_DAYS,
  buildPendingLicenseDocument,
  buildUnusedMigrationUpdate,
  getLicenseLifecycle,
  isMigratableUnusedLicense,
} = require("../lib/license_lifecycle");
const {
  LicenseInputError,
  issueLicenseBatch,
  normalizeLicenseTerm,
  serializeLicenseForAdmin,
} = require("../lib/license_admin");

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
const timestampFromMillis = (value) => admin.firestore.Timestamp.fromMillis(value);

function unauthorized(res) {
  return res.status(401).json({ success: false, message: "인증 실패" });
}

async function migrationTargets() {
  const snapshot = await db.collection("licenses").get();
  return {
    snapshot,
    targets: snapshot.docs.filter((doc) => isMigratableUnusedLicense(doc.data())),
  };
}

async function applyUnusedMigration(targets, now) {
  let migrated = 0;
  for (let index = 0; index < targets.length; index += 400) {
    const batch = db.batch();
    for (const doc of targets.slice(index, index + 400)) {
      batch.set(
        doc.ref,
        buildUnusedMigrationUpdate(doc.data(), now, timestampFromMillis),
        { merge: true }
      );
    }
    await batch.commit();
    migrated += Math.min(400, targets.length - index);
  }
  return migrated;
}

module.exports = async function handler(req, res) {
  sec.setSecurityHeaders(res);
  const corsOk = sec.applyCors(req, res, ALLOWED_ORIGINS);
  if (req.method === "OPTIONS") return res.status(204).end();
  if (!corsOk) {
    return res.status(403).json({ success: false, message: "허용되지 않은 출처입니다." });
  }

  const ip = sec.getClientIp(req);
  const rl = await sec.rateLimit(db, {
    bucket: "admin",
    ip,
    max: 30,
    windowMs: 60000,
    failClosed: true,
  });
  if (!rl.allowed) {
    return res.status(429).json({ success: false, message: "요청이 너무 많습니다." });
  }
  if (!sec.verifyAdminKey(req)) return unauthorized(res);

  const col = db.collection("licenses");

  if (req.method === "GET") {
    try {
      const snapshot = await col.orderBy("createdAt", "desc").get();
      const now = Date.now();
      const licenses = snapshot.docs.map((doc) =>
        serializeLicenseForAdmin(doc.id, doc.data(), now)
      );
      return res.status(200).json({ success: true, licenses });
    } catch (err) {
      console.error("Admin list error:", err);
      return res.status(500).json({ success: false, message: "목록 조회 실패" });
    }
  }

  if (req.method === "POST") {
    const body = req.body || {};
    try {
      if (body.action === "batchIssue") {
        const result = await issueLicenseBatch({
          db,
          input: body,
          timestampFromMillis,
        });
        return res.status(result.repeated ? 200 : 201).json({
          success: true,
          message: result.repeated
            ? "같은 요청의 기존 발급 결과를 불러왔습니다."
            : `${result.term} ${result.count}개를 발급했습니다.`,
          ...result,
          plainText: result.keys.join("\n"),
        });
      }

      const key = String(body.key || "").trim();
      const licenseTerm = normalizeLicenseTerm(body);
      const memo = body.memo === undefined ? "" : body.memo;
      if (!sec.isValidKeyFormat(key)) {
        return res.status(400).json({ success: false, message: "키 형식이 올바르지 않습니다." });
      }
      if (typeof memo !== "string" || memo.length > 200) {
        return res.status(400).json({ success: false, message: "메모는 200자 이하여야 합니다." });
      }

      const now = Date.now();
      await col.doc(key).create(
        buildPendingLicenseDocument({
          ...licenseTerm,
          issuedAt: now,
          timestampFromMillis,
          memo,
          batchName: "단건 발급",
        })
      );
      return res.status(201).json({
        success: true,
        message: `${getLicenseLifecycle(licenseTerm).term} 라이센스를 발급했습니다.`,
      });
    } catch (err) {
      if (err.code === 6 || err.code === "already-exists") {
        return res.status(409).json({ success: false, message: "이미 존재하는 키입니다." });
      }
      if (err instanceof LicenseInputError) {
        return res.status(400).json({ success: false, message: err.message });
      }
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
      return res.status(200).json({ success: true, message: "라이센스를 삭제했습니다." });
    } catch (err) {
      console.error("Admin delete error:", err);
      return res.status(500).json({ success: false, message: "삭제 실패" });
    }
  }

  if (req.method === "PATCH") {
    const body = req.body || {};
    try {
      if (body.action === "previewUnusedMigration") {
        const { snapshot, targets } = await migrationTargets();
        return res.status(200).json({
          success: true,
          total: snapshot.size,
          eligible: targets.length,
          protectedUsed: snapshot.size - targets.length,
          sample: targets.slice(0, 5).map((doc) => doc.id),
        });
      }
      if (body.action === "migrateUnused") {
        if (body.confirm !== true) {
          return res.status(400).json({ success: false, message: "마이그레이션 확인값이 필요합니다." });
        }
        const { snapshot, targets } = await migrationTargets();
        const migrated = await applyUnusedMigration(targets, Date.now());
        return res.status(200).json({
          success: true,
          message: `미사용 키 ${migrated}개를 첫 인증 대기로 전환했습니다.`,
          total: snapshot.size,
          migrated,
          protectedUsed: snapshot.size - targets.length,
        });
      }

      const { key } = body;
      if (!sec.isValidKeyFormat(key)) {
        return res.status(400).json({ success: false, message: "키 형식이 올바르지 않습니다." });
      }
      const doc = await col.doc(key).get();
      if (!doc.exists) {
        return res.status(404).json({ success: false, message: "존재하지 않는 키입니다." });
      }

      if (body.resetHwids === true) {
        await col.doc(key).update({ hwids: [] });
        return res.status(200).json({ success: true, message: "HWID를 초기화했습니다." });
      }

      if (body.extendDays !== undefined) {
        const extendDays = Number(body.extendDays);
        if (!Number.isInteger(extendDays) || extendDays < 1 || extendDays > 36500) {
          return res.status(400).json({ success: false, message: "연장 일수는 1~36500일이어야 합니다." });
        }
        const data = doc.data();
        const lifecycle = getLicenseLifecycle(data);
        if (lifecycle.unlimited) {
          return res.status(400).json({ success: false, message: "이미 무제한 라이센스입니다." });
        }
        if (lifecycle.pending) {
          const newDays = lifecycle.hours > 0 ? 0 : lifecycle.days + extendDays;
          const newHours = lifecycle.hours > 0 ? lifecycle.hours + extendDays * 24 : 0;
          await col.doc(key).update({ days: newDays, hours: newHours });
          return res.status(200).json({
            success: true,
            message: `첫 인증 전 이용기간을 ${getLicenseLifecycle({ days:newDays, hours:newHours }).term}으로 변경했습니다.`,
          });
        }

        const now = Date.now();
        const base = Math.max(now, lifecycle.expiresAt);
        const newExpiry = base + extendDays * DAY_MS;
        const activationBase = lifecycle.activatedAt || now;
        const hourly = lifecycle.hours > 0;
        const newDays = hourly ? 0 : Math.max(1, Math.round((newExpiry - activationBase) / DAY_MS));
        const newHours = hourly ? Math.max(1, Math.round((newExpiry - activationBase) / (60 * 60 * 1000))) : 0;
        await col.doc(key).update({
          days: newDays,
          hours: newHours,
          expiresAt: timestampFromMillis(newExpiry),
        });
        return res.status(200).json({
          success: true,
          message: `${extendDays}일 연장했습니다. (잔여 약 ${Math.ceil((newExpiry - now) / DAY_MS)}일)`,
        });
      }

      if (body.makeUnlimited === true) {
        await col.doc(key).update({ days: UNLIMITED_DAYS, hours: 0, expiresAt: null });
        return res.status(200).json({ success: true, message: "무제한으로 전환했습니다." });
      }

      if (body.discordId !== undefined) {
        if (!sec.isValidDiscordId(body.discordId)) {
          return res.status(400).json({ success: false, message: "디스코드 ID 형식이 올바르지 않습니다." });
        }
        await col.doc(key).update({ discordId: body.discordId || "" });
        return res.status(200).json({ success: true, message: "디스코드 ID를 수정했습니다." });
      }

      await col.doc(key).update({ disabled: body.disabled === true });
      return res.status(200).json({
        success: true,
        message: body.disabled ? "라이센스를 비활성화했습니다." : "라이센스를 활성화했습니다.",
      });
    } catch (err) {
      console.error("Admin update error:", err);
      return res.status(500).json({ success: false, message: "수정 실패" });
    }
  }

  return res.status(405).json({ success: false, message: "Method Not Allowed" });
};
