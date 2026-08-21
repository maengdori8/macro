"use strict";

// admin 핸들러 배선 — 키별 2점차 자동 종료 비율 PATCH 가 **실제 문서**에 닿는지 본다.
//
// verify_handler.test.js 와 같은 이유·같은 방식이다: lib 단위 테스트는 규칙만 보증하고,
// 핸들러가 분기를 빠뜨리면(예: autoExitRatio 를 안 보고 disabled 토글로 떨어짐)
// 기능이 조용히 죽는다. firebase-admin 은 Module._load 후킹으로 스텁을 꽂는다.

const assert = require("node:assert/strict");
const test = require("node:test");
const Module = require("node:module");
const { FIRST_VERIFY_POLICY } = require("../lib/license_lifecycle");
const { FakeFirestore } = require("./fake_firestore");

const db = new FakeFirestore();
const fakeAdmin = {
  apps: [{}],
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
process.env.ADMIN_KEY = "test-admin-key-0123456789";
const handler = require("../api/admin");
const sec = require("../lib/security");

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

async function patch(body, adminKey = process.env.ADMIN_KEY) {
  sec.resetRateLimitsForTests();
  const res = makeRes();
  await handler(
    {
      method: "PATCH",
      body,
      headers: { "x-admin-key": adminKey },
      socket: { remoteAddress: "9.9.9.9" },
    },
    res
  );
  return res;
}

test("프로 키에 키별 비율을 저장하고, 해제하면 null 로 돌아간다", async () => {
  const key = "PRO11-PRO11-PRO11-PRO11-PRO11";
  db._docs.set(`licenses/${key}`, licenseDoc({ product: "macro_pro" }));

  let res = await patch({ key, autoExitRatio: 0.6 });
  assert.equal(res.statusCode, 200, JSON.stringify(res.body));
  assert.equal(db.read(`licenses/${key}`).autoExitRatio, 0.6);
  assert.match(res.body.message, /60%/);
  // 다른 필드는 건드리지 않는다(disabled 토글 분기로 떨어지지 않았다).
  assert.equal(db.read(`licenses/${key}`).disabled, false);

  res = await patch({ key, autoExitRatio: null });
  assert.equal(res.statusCode, 200, JSON.stringify(res.body));
  assert.equal(db.read(`licenses/${key}`).autoExitRatio, null);
});

test("범위 밖 값은 거부되고 문서가 바뀌지 않는다", async () => {
  const key = "PRO22-PRO22-PRO22-PRO22-PRO22";
  db._docs.set(`licenses/${key}`, licenseDoc({ product: "macro_pro", autoExitRatio: 0.3 }));
  for (const bad of [1.5, -0.1, "abc", ""]) {
    const res = await patch({ key, autoExitRatio: bad });
    assert.equal(res.statusCode, 400, `거부돼야 하는 값: ${String(bad)}`);
  }
  assert.equal(db.read(`licenses/${key}`).autoExitRatio, 0.3);
});

test("일반·mPause 키에는 설정할 수 없다", async () => {
  const cases = [
    ["BASIC-BASIC-BASIC-BASIC-BASIC", { product: "macro" }],
    ["PAUSE-PAUSE-PAUSE-PAUSE-PAUSE", { product: "mpause" }],
    ["OLDKY-OLDKY-OLDKY-OLDKY-OLDKY", {}], // 제품 필드 없는 옛 문서 = macro
  ];
  for (const [key, extra] of cases) {
    db._docs.set(`licenses/${key}`, licenseDoc(extra));
    const res = await patch({ key, autoExitRatio: 0.5 });
    assert.equal(res.statusCode, 400, `${key}: ${JSON.stringify(res.body)}`);
    assert.equal("autoExitRatio" in db.read(`licenses/${key}`), false);
  }
});

test("없는 키는 404, 관리자 키가 틀리면 401", async () => {
  let res = await patch({ key: "NONE1-NONE1-NONE1-NONE1-NONE1", autoExitRatio: 0.5 });
  assert.equal(res.statusCode, 404);
  res = await patch({ key: "PRO11-PRO11-PRO11-PRO11-PRO11", autoExitRatio: 0.5 }, "wrong");
  assert.equal(res.statusCode, 401);
});
