"use strict";

// 업데이트 기록을 제품별로 나눈다.
//
// 왜 중요한가: 기록이 하나뿐이면 프로를 릴리스하는 순간 **일반 사용자의 런처가
// 프로 설치본을 내려받는다.** 오류도, 경고도 없이 그렇게 된다. 반대로 기존 런처는
// product 를 보내지 않으므로, 그 요청은 지금 문서를 그대로 봐야 한다(영향 0).

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

// 핸들러는 firebase-admin 초기화를 요구하므로, 문서 이름 규칙만 떼어 검증한다.
const source = fs.readFileSync(
  path.join(__dirname, "..", "api", "version.js"),
  "utf8"
);
const fnSource = source.match(/const PRODUCT_PATTERN[\s\S]*?\n\}/)[0];
const context = vm.createContext({});
vm.runInContext(fnSource, context);
const versionDocId = (product) =>
  vm.runInContext(`versionDocId(${JSON.stringify(product)})`, context);

test("기존 런처(제품 미전송)는 지금 쓰던 문서를 그대로 본다", () => {
  assert.equal(versionDocId(undefined), "version");
  assert.equal(versionDocId(""), "version");
  assert.equal(versionDocId(null), "version");
});

test("일반 mAuto 는 같은 문서를 쓴다(마이그레이션 불필요)", () => {
  assert.equal(versionDocId("macro"), "version");
  assert.equal(versionDocId("MACRO"), "version");
});

test("프로는 별도 문서를 쓴다 — 일반 사용자에게 프로 설치본이 가면 안 된다", () => {
  assert.equal(versionDocId("macro_pro"), "version_macro_pro");
  assert.notEqual(versionDocId("macro_pro"), versionDocId("macro"));
});

test("이상한 제품 값은 거부한다(문서 이름 주입 차단)", () => {
  for (const bad of ["../config", "a/b", "a b", "A".repeat(33), "제품", "a|b"]) {
    assert.equal(versionDocId(bad), null, `막지 못했다: ${bad}`);
  }
});

test("관리 화면도 제품을 함께 보낸다", () => {
  // 화면에서 제품을 안 보내면, 프로 등록이 일반 기록을 덮어써서
  // 일반 사용자 런처가 프로 설치본을 받아 간다.
  const admin = fs.readFileSync(path.join(__dirname, "..", "admin.html"), "utf8");
  assert.match(admin, /id="versionProductInput"/);
  assert.match(admin, /product:\$\("versionProductInput"\)\.value/);
  assert.match(admin, /\/api\/version\?product=\$\{encodeURIComponent\(product\)\}/);
});


test("GET·POST 양쪽이 같은 규칙을 쓴다", () => {
  // 한쪽만 나누면 등록은 프로 문서에 되는데 조회는 일반 문서에서 나온다(또는 반대).
  assert.match(source, /const docId = versionDocId\(req\.query && req\.query\.product\)/);
  assert.match(source, /const docId = versionDocId\(req\.body && req\.body\.product\)/);
  assert.match(source, /db\.collection\("config"\)\.doc\(docId\)/);
  assert.doesNotMatch(source, /doc\("version"\)/, "아직 문서 이름이 하드코딩돼 있다");
});
