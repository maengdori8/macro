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


test("키별 exitSettings: 보낸 필드만 저장, null 은 해제, 불량·모르는 필드는 거부", async () => {
  const key = "PRO33-PRO33-PRO33-PRO33-PRO33";
  db._docs.set(`licenses/${key}`, licenseDoc({ product: "macro_pro", autoExitRatio: 0.5 }));

  let res = await patch({ key, exitSettings: { autoExitHardDeficit: 4, autoExitLateMinute: 75, autoExitLateRatio: 1 } });
  assert.equal(res.statusCode, 200, JSON.stringify(res.body));
  let doc = db.read(`licenses/${key}`);
  assert.equal(doc.autoExitHardDeficit, 4);
  assert.equal(doc.autoExitLateMinute, 75);
  assert.equal(doc.autoExitLateRatio, 1);
  assert.equal(doc.autoExitRatio, 0.5, "안 보낸 필드는 그대로");
  assert.equal(res.body.exitSettings.autoExitHardDeficit, 4);
  assert.equal(res.body.exitSettings.autoExitRatio, 0.5);

  res = await patch({ key, exitSettings: { autoExitHardDeficit: null } });
  assert.equal(res.statusCode, 200);
  assert.equal(db.read(`licenses/${key}`).autoExitHardDeficit, null);

  for (const bad of [{ autoExitHardDeficit: 99 }, { autoExitLateMinute: -1 }, { bogus: 1 }, {}, "junk", [1]]) {
    res = await patch({ key, exitSettings: bad });
    assert.equal(res.statusCode, 400, `거부돼야 함: ${JSON.stringify(bad)}`);
  }
  assert.equal(db.read(`licenses/${key}`).autoExitLateMinute, 75, "거부된 요청이 문서를 바꿨다");

  // 일반 키에는 안 된다.
  const basic = "BAS33-BASIC-BASIC-BASIC-BASIC";
  db._docs.set(`licenses/${basic}`, licenseDoc({ product: "macro" }));
  res = await patch({ key: basic, exitSettings: { autoExitHardDeficit: 4 } });
  assert.equal(res.statusCode, 400);
});

test("전역 setProSettings: 여러 필드 저장·null 해제·불량 거부, 응답은 실효값", async () => {
  let res = await patch({ action: "setProSettings", autoExitRatio: 0.6, autoExitHardDeficit: 5, autoExitLateRatio: null });
  assert.equal(res.statusCode, 200, JSON.stringify(res.body));
  assert.equal(res.body.proSettings.autoExitRatio, 0.6);
  assert.equal(res.body.proSettings.autoExitHardDeficit, 5);
  assert.equal(res.body.proSettings.autoExitLateRatio, null);
  assert.equal(res.body.proSettings.autoExitBaseDeficit, 2, "안 보낸 필드는 기본값으로 채워져 온다");
  const doc = db.read("config/pro_settings");
  assert.equal(doc.autoExitRatio, 0.6);
  assert.equal(doc.autoExitHardDeficit, 5);

  res = await patch({ action: "setProSettings", autoExitHardDeficit: 42 });
  assert.equal(res.statusCode, 400);
  res = await patch({ action: "setProSettings" });
  assert.equal(res.statusCode, 400, "보낸 필드가 없으면 거부");
  // 옛 형태(비율만)도 그대로 된다.
  res = await patch({ action: "setProSettings", autoExitRatio: 0.35 });
  assert.equal(res.statusCode, 200);
  assert.equal(res.body.proSettings.autoExitRatio, 0.35);
});


test("모르는 action 은 400 — key 경로(disabled 토글)로 떨어지지 않는다", async () => {
  const key = "PRO44-PRO44-PRO44-PRO44-PRO44";
  db._docs.set(`licenses/${key}`, licenseDoc({ product: "macro_pro", disabled: true }));
  const res = await patch({ action: "setProSetings", key, autoExitRatio: 0.5 }); // 오타
  assert.equal(res.statusCode, 400, JSON.stringify(res.body));
  assert.equal(db.read(`licenses/${key}`).disabled, true, "비활성 키가 켜졌다");
  assert.equal(db.read(`licenses/${key}`).autoExitRatio, undefined);
});
