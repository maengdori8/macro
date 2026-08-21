"use strict";

// verify 핸들러 배선 — 운영 설정(auto_exit_ratio)이 **실제 응답**에 실리는지 본다.
//
// 왜 필요한가: pro_settings.test.js 는 "읽으면 값이 나온다"까지만 보증한다.
// 핸들러가 extra 를 sendSignedVerdict 에 전달하는 걸 잊으면 기능이 조용히 죽는데
// (응답에 필드만 빠지고 인증은 멀쩡), 그 배선은 여기서만 잡힌다.
//
// api/verify.js 는 로드 시점에 firebase-admin 을 초기화한다. 로컬에는 그 패키지가
// 없으므로(배포 환경에서만 설치) Module._load 를 후킹해 스텁을 꽂는다 — node:test
// 러너는 파일마다 별도 프로세스라 이 후킹이 다른 테스트 파일로 새지 않는다.

const assert = require("node:assert/strict");
const test = require("node:test");
const Module = require("node:module");
const { FIRST_VERIFY_POLICY } = require("../lib/license_lifecycle");
const { writeAutoExitRatio } = require("../lib/pro_settings");
const { FakeFirestore } = require("./fake_firestore");

const db = new FakeFirestore();
const fakeAdmin = {
  apps: [{}], // 이미 초기화된 것으로 보이게 해 자격 증명 경로를 건너뛴다.
  initializeApp() {},
  credential: { cert: () => ({}) },
  firestore: Object.assign(() => db, {
    Timestamp: { fromMillis: (value) => value },
    FieldValue: { serverTimestamp: () => "server-time" },
  }),
};

const originalLoad = Module._load;
Module._load = function (request, ...rest) {
  if (request === "firebase-admin") return fakeAdmin;
  return originalLoad.call(this, request, ...rest);
};
process.env.LICENSE_SIGNING_SEED = "11".repeat(32);
const handler = require("../api/verify");
const sec = require("../lib/security");

const HWID = "a".repeat(32);
const NONCE = "b".repeat(32);

function licenseDoc(extra = {}) {
  const issuedAt = Date.now() - 1000;
  return {
    days: 30,
    createdAt: issuedAt,
    issuedAt,
    activationPolicy: FIRST_VERIFY_POLICY,
    activatedAt: null,
    expiresAt: null,
    disabled: false,
    hwids: [],
    maxHwids: 3,
    ...extra,
  };
}

function makeRes() {
  return {
    statusCode: 0,
    body: null,
    headers: {},
    setHeader(name, value) {
      this.headers[name] = value;
    },
    status(code) {
      this.statusCode = code;
      return this;
    },
    json(value) {
      this.body = value;
      return this;
    },
    end() {
      return this;
    },
  };
}

async function callVerify(body) {
  sec.resetRateLimitsForTests();
  const res = makeRes();
  await handler(
    { method: "POST", body, headers: {}, socket: { remoteAddress: "1.2.3.4" } },
    res
  );
  return res;
}

test("프로 키 응답에 자동 종료 비율이 실린다", async () => {
  const key = "AAAAA-AAAAA-AAAAA-AAAAA-AAAAA";
  db._docs.set(`licenses/${key}`, licenseDoc({ product: "macro_pro" }));
  // writeAutoExitRatio 를 쓰는 이유: 문서를 직접 심으면 읽기 캐시(TTL)에 안 보인다.
  await writeAutoExitRatio(db, 0.3);

  const res = await callVerify({ key, hwid: HWID, nonce: NONCE, product: "macro_pro" });
  assert.equal(res.statusCode, 200);
  assert.equal(res.body.verdict, "valid", res.body.message);
  assert.equal(res.body.auto_exit_ratio, 0.3, "운영 비율이 응답에 안 실렸다");
  // 서명 대상 필드는 extra 에 밀리지 않고 온전해야 한다.
  assert.equal(res.body.product, "macro_pro");
  assert.match(res.body.sig, /^[0-9a-f]{128}$/);
});

test("일반 키 응답에는 비율이 없다(프로 전용 운영값)", async () => {
  const key = "BBBBB-BBBBB-BBBBB-BBBBB-BBBBB";
  db._docs.set(`licenses/${key}`, licenseDoc({ product: "macro" }));

  const res = await callVerify({ key, hwid: HWID, nonce: NONCE, product: "macro" });
  assert.equal(res.body.verdict, "valid", res.body.message);
  assert.equal("auto_exit_ratio" in res.body, false, "일반 응답에 프로 운영값이 샜다");
});

test("구버전 클라이언트(v1, product 없음) 응답도 오염되지 않는다", async () => {
  const key = "CCCCC-CCCCC-CCCCC-CCCCC-CCCCC";
  db._docs.set(`licenses/${key}`, licenseDoc());

  const res = await callVerify({ key, hwid: HWID, nonce: NONCE });
  assert.equal(res.body.verdict, "valid", res.body.message);
  assert.equal(res.body.product, "", "v1 응답의 product 는 빈 값이어야 한다");
  assert.equal("auto_exit_ratio" in res.body, false);
});

test("라이센스 문서의 키별 비율이 전역값보다 우선해 실린다", async () => {
  const key = "EEEEE-EEEEE-EEEEE-EEEEE-EEEEE";
  db._docs.set(`licenses/${key}`, licenseDoc({ product: "macro_pro", autoExitRatio: 0.15 }));
  await writeAutoExitRatio(db, 0.3);

  const res = await callVerify({ key, hwid: HWID, nonce: NONCE, product: "macro_pro" });
  assert.equal(res.body.verdict, "valid", res.body.message);
  assert.equal(res.body.auto_exit_ratio, 0.15, "키별 비율이 아니라 전역값이 실렸다");
  // 서명 대상 필드는 그대로다.
  assert.equal(res.body.product, "macro_pro");
  assert.match(res.body.sig, /^[0-9a-f]{128}$/);
});

test("키별 비율이 깨져 있으면 전역값으로 떨어진다(인증은 막히지 않는다)", async () => {
  const key = "FFFFF-FFFFF-FFFFF-FFFFF-FFFFF";
  db._docs.set(`licenses/${key}`, licenseDoc({ product: "macro_pro", autoExitRatio: 7 }));
  await writeAutoExitRatio(db, 0.3);

  const res = await callVerify({ key, hwid: HWID, nonce: NONCE, product: "macro_pro" });
  assert.equal(res.body.verdict, "valid", res.body.message);
  assert.equal(res.body.auto_exit_ratio, 0.3);
});

test("일반 키에 키별 비율이 적혀 있어도 응답에 새지 않는다", async () => {
  const key = "GGGGG-GGGGG-GGGGG-GGGGG-GGGGG";
  db._docs.set(`licenses/${key}`, licenseDoc({ product: "macro", autoExitRatio: 0.2 }));

  const res = await callVerify({ key, hwid: HWID, nonce: NONCE, product: "macro" });
  assert.equal(res.body.verdict, "valid", res.body.message);
  assert.equal("auto_exit_ratio" in res.body, false);
});

test("프로 키를 일반 요청으로 써도(하위 포함) 비율이 실린다 — 문서 기준이다", async () => {
  const key = "DDDDD-DDDDD-DDDDD-DDDDD-DDDDD";
  db._docs.set(`licenses/${key}`, licenseDoc({ product: "macro_pro" }));
  await writeAutoExitRatio(db, 0.3);

  const res = await callVerify({ key, hwid: HWID, nonce: NONCE, product: "macro" });
  assert.equal(res.body.verdict, "valid", res.body.message);
  // 일반 빌드는 이 값을 안 쓰지만(무해), 판정 기준이 요청이 아니라 문서임을 고정한다.
  assert.equal(res.body.auto_exit_ratio, 0.3);
});
