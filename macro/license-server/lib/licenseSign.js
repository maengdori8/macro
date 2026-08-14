"use strict";

// 라이센스 응답 Ed25519 서명 (크랙/가짜서버 방지).
//
// 왜: 응답이 서명 안 되면 hosts 파일로 도메인을 로컬로 돌리고 {"valid":true}만 뱉는
// 가짜 서버로 바이너리 패치 없이 크랙된다. 개인키는 이 서버 환경변수(LICENSE_SIGNING_SEED)
// 에만 있고, 클라이언트는 박아둔 공개키로만 검증하므로 가짜 서버는 서명을 위조할 수 없다.
//
// 메시지 규칙은 클라이언트(macroapp/license_client.py:_license_message)와 정확히 같아야 한다:
//   "license-v1|{verdict}|{hwid}|{nonce}|{exp}"
// verdict = "valid" | "invalid", hwid/nonce = hex, exp = 정수(초). 전부 [0-9a-z|]라 구분자 주입 불가.

const crypto = require("crypto");

const PREFIX = "license-v1";
// Ed25519 32바이트 seed를 PKCS8 DER로 감싸는 고정 프리픽스(RFC 8410).
const PKCS8_ED25519_PREFIX = Buffer.from("302e020100300506032b657004220420", "hex");

function loadPrivateKey() {
  const hex = (process.env.LICENSE_SIGNING_SEED || "").trim();
  if (!/^[0-9a-fA-F]{64}$/.test(hex)) {
    // 키가 없으면 서명할 수 없다. 여기서 던져 상위에서 500으로 처리(정상 응답을
    // 서명 없이 내보내면 새 클라이언트가 전부 거부되므로, 조용히 넘기지 않는다).
    throw new Error("LICENSE_SIGNING_SEED가 설정되지 않았거나 형식이 잘못되었습니다.");
  }
  const der = Buffer.concat([PKCS8_ED25519_PREFIX, Buffer.from(hex, "hex")]);
  return crypto.createPrivateKey({ key: der, format: "der", type: "pkcs8" });
}

// verdict 응답 하나를 서명해 JSON으로 내보낸다. 정상/거부 판정 모두 이 경로를 쓰면
// verdict 자체가 서명에 묶여, MITM이 valid를 invalid로 뒤집는 것(DoS)도 차단된다.
function sendSignedVerdict(res, { verdict, hwid, nonce, exp, message }) {
  const safeExp = Number.isFinite(exp) ? Math.floor(exp) : 0;
  const msg = `${PREFIX}|${verdict}|${hwid}|${nonce}|${safeExp}`;
  const sig = crypto.sign(null, Buffer.from(msg, "utf8"), loadPrivateKey());
  return res.status(200).json({
    valid: verdict === "valid",   // 참고용(클라이언트는 서명된 verdict만 신뢰)
    verdict,
    hwid,
    nonce,
    exp: safeExp,
    message: message || "",
    sig: sig.toString("hex"),
  });
}

module.exports = { sendSignedVerdict, PREFIX };
