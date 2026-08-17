"use strict";

const assert = require("node:assert/strict");
const test = require("node:test");
const {
  LicenseInputError,
  issueLicenseBatch,
  normalizeBatchInput,
} = require("../lib/license_admin");
const { isCanonicalLicenseKey } = require("../lib/license_lifecycle");
const { FakeFirestore } = require("./fake_firestore");

test("발급 입력은 기간·수량·메모 한도를 강제한다", () => {
  assert.throws(
    () => normalizeBatchInput({ days: 30, count: 0, requestId: "abcdefgh" }),
    (error) => error instanceof LicenseInputError && error.code === "LICENSE_INPUT"
  );
  assert.throws(() => normalizeBatchInput({ days: 0, count: 10, requestId: "abcdefgh" }), /기간/);
  assert.throws(() => normalizeBatchInput({ days: 30, count: 10, requestId: "bad" }), /ID/);
  assert.equal(normalizeBatchInput({ days: 30, count: 100, requestId: "abcdefgh" }).count, 100);
  assert.deepEqual(
    normalizeBatchInput({ hours: 3, count: 10, requestId: "hourbatch001" }),
    { days:0, hours:3, count:10, requestId:"hourbatch001", batchName:"", memo:"", product:"macro" }
  );
  // 제품을 지정하지 않으면 기존과 같이 mAuto("macro") 키가 나온다(하위 호환).
  assert.equal(normalizeBatchInput({ days: 30, count: 1, requestId: "prodbatch001" }).product, "macro");
  assert.equal(
    normalizeBatchInput({ days: 30, count: 1, requestId: "prodbatch002", product: "mPause" }).product,
    "mpause"
  );
  // 구분자 주입은 물론이고, 형식은 맞지만 **아는 제품이 아닌 값**도 막아야 한다.
  // 오타로 발급된 키는 어느 앱에서도 안 열리는데 그걸 고객이 먼저 발견하게 된다.
  assert.throws(
    () => normalizeBatchInput({ days: 30, count: 1, requestId: "prodbatch003", product: "bad|inject" }),
    /발급할 수 없는 제품/
  );
  assert.throws(
    () => normalizeBatchInput({ days: 30, count: 1, requestId: "prodbatch004", product: "macropro" }),
    /발급할 수 없는 제품/
  );
  // 프로는 아직 클라이언트가 없지만 재고를 미리 만들 수 있어야 한다.
  assert.equal(
    normalizeBatchInput({ days: 30, count: 1, requestId: "prodbatch005", product: "macro_pro" }).product,
    "macro_pro"
  );
  assert.throws(() => normalizeBatchInput({ hours: 25, count: 1, requestId: "hourbatch002" }), /1~24시간/);
  assert.throws(() => normalizeBatchInput({ days: 1, hours: 3, count: 1, requestId: "hourbatch003" }), /동시에/);
});

test("시간권 일괄발급도 첫 인증 대기로 저장되고 재시도 시 중복되지 않는다", async () => {
  const db = new FakeFirestore();
  const input = { hours:6, count:4, requestId:"hourbatch004", batchName:"6시간 자판기", memo:"" };
  const first = await issueLicenseBatch({ db, input, now:1000, timestampFromMillis:(value) => value });
  const second = await issueLicenseBatch({ db, input, now:9999, timestampFromMillis:(value) => value });
  assert.equal(first.term, "6시간권");
  assert.equal(second.term, "6시간권");
  assert.equal(second.repeated, true);
  assert.deepEqual(second.keys, first.keys);
  assert.equal(db.count("licenses"), 4);
  for (const key of first.keys) {
    const stored = db.read(`licenses/${key}`);
    assert.equal(stored.days, 0);
    assert.equal(stored.hours, 6);
    assert.equal(stored.activatedAt, null);
  }
});

test("100개 일괄발급은 정확한 줄 수와 고유 키를 만든다", async () => {
  const db = new FakeFirestore();
  const result = await issueLicenseBatch({
    db,
    input: {
      days: 30,
      count: 100,
      requestId: "batchrequest0001",
      batchName: "30일권 재고",
      memo: "자판기",
    },
    now: 1000,
    timestampFromMillis: (value) => value,
  });
  assert.equal(result.count, 100);
  assert.equal(new Set(result.keys).size, 100);
  assert.equal(result.keys.every(isCanonicalLicenseKey), true);
  assert.equal(result.keys.join("\n").split("\n").length, 100);
  assert.equal(db.count("licenses"), 100);
  for (const key of result.keys) {
    const stored = db.read(`licenses/${key}`);
    assert.equal(stored.activatedAt, null);
    assert.equal(stored.days, 30);
  }
});

test("같은 requestId 재시도는 기존 키만 반환한다", async () => {
  const db = new FakeFirestore();
  const input = {
    days: 30,
    count: 5,
    requestId: "batchrequest0002",
    batchName: "재시도",
    memo: "",
  };
  const first = await issueLicenseBatch({
    db, input, now: 1000, timestampFromMillis: (value) => value,
  });
  const second = await issueLicenseBatch({
    db, input, now: 9999, timestampFromMillis: (value) => value,
  });
  assert.deepEqual(second.keys, first.keys);
  assert.equal(second.repeated, true);
  assert.equal(db.count("licenses"), 5);
  assert.equal(db.count("licenseBatches"), 1);
});
