"use strict";

// 라이센스 응답 Ed25519 서명 (크랙/가짜서버 방지).
//
// 왜: 응답이 서명 안 되면 hosts 파일로 도메인을 로컬로 돌리고 {"valid":true}만 뱉는
// 가짜 서버로 바이너리 패치 없이 크랙된다. 개인키는 이 서버 환경변수(LICENSE_SIGNING_SEED)
// 에만 있고, 클라이언트는 박아둔 공개키로만 검증하므로 가짜 서버는 서명을 위조할 수 없다.
//
// 메시지 규칙은 클라이언트의 _license_message 와 정확히 같아야 한다. 두 가지가 있다:
//   v1 (mAuto, macroapp/license_client.py)   "license-v1|{verdict}|{hwid}|{nonce}|{exp}"
//   v2 (제품 바인딩, mpauseapp/license_client.py)
//                                            "license-v2|{product}|{verdict}|{hwid}|{nonce}|{exp}"
// verdict = "valid" | "invalid", hwid/nonce = hex, exp = 정수(초), product = [a-z0-9_-]{1,32}.
// 전부 [0-9a-z|_-]라 구분자 주입 불가.
//
// ⚠️ 호환성: 요청에 product 가 없으면(구버전 mAuto 클라이언트) 반드시 v1 으로 서명한다.
// v2 로 바꿔 버리면 이미 배포된 mAuto 가 전부 서명 검증에 실패해 정품 사용자가 막힌다.

const crypto = require("crypto");
const { isValidProduct } = require("./product");

const PREFIX = "license-v1";
const PREFIX_V2 = "license-v2";
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

// 서명 대상 문자열을 만든다. product 가 있으면 v2, 없으면 v1(구버전 호환).
function buildSignedMessage({ product, verdict, hwid, nonce, exp }) {
  if (product) {
    if (!isValidProduct(product)) {
      // 여기까지 잘못된 값이 오면 서명하지 않는다(구분자 주입 방지의 마지막 관문).
      throw new Error("product 형식이 올바르지 않습니다.");
    }
    return `${PREFIX_V2}|${product}|${verdict}|${hwid}|${nonce}|${exp}`;
  }
  return `${PREFIX}|${verdict}|${hwid}|${nonce}|${exp}`;
}

// verdict 응답 하나를 서명해 JSON으로 내보낸다. 정상/거부 판정 모두 이 경로를 쓰면
// verdict 자체가 서명에 묶여, MITM이 valid를 invalid로 뒤집는 것(DoS)도 차단된다.
//
// extra: 서명 **밖** 참고값(운영 설정 등). 신뢰 판정을 여기 실으면 안 된다 —
// 클라이언트도 참고값으로만 다루고 범위 밖 값은 버린다.
function sendSignedVerdict(res, { verdict, hwid, nonce, exp, message, product }, extra = {}) {
  const safeExp = Number.isFinite(exp) ? Math.floor(exp) : 0;
  const msg = buildSignedMessage({ product, verdict, hwid, nonce, exp: safeExp });
  const sig = crypto.sign(null, Buffer.from(msg, "utf8"), loadPrivateKey());
  return res.status(200).json({
    // extra 를 먼저 펼친다 — 실수로도 서명 대상 필드(verdict/hwid/nonce/exp/sig)를
    // 덮어쓸 수 없게 핵심 필드가 항상 이긴다.
    ...extra,
    valid: verdict === "valid",   // 참고용(클라이언트는 서명된 verdict만 신뢰)
    verdict,
    hwid,
    nonce,
    exp: safeExp,
    // 참고용. 클라이언트는 이 값을 읽지 않고 자기 상수로 메시지를 만든다
    // (응답에서 읽으면 공격자가 바꿔 끼울 여지가 생긴다).
    product: product || "",
    message: message || "",
    sig: sig.toString("hex"),
  });
}

module.exports = { sendSignedVerdict, buildSignedMessage, PREFIX, PREFIX_V2 };
