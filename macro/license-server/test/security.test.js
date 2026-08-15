"use strict";

const assert = require("node:assert/strict");
const Module = require("node:module");
const test = require("node:test");

const originalLoad = Module._load;
Module._load = function loadWithoutFirebase(request, parent, isMain) {
  if (request === "firebase-admin") return {};
  return originalLoad.call(this, request, parent, isMain);
};
const security = require("../lib/security");
Module._load = originalLoad;

function fakeResponse() {
  return {
    headers: {},
    setHeader(name, value) {
      this.headers[name] = value;
    },
  };
}

test("Vercel 프리뷰의 동일 출처 관리자 요청은 허용한다", () => {
  const res = fakeResponse();
  const allowed = security.applyCors(
    {
      headers: {
        origin: "https://preview-abc.vercel.app",
        host: "preview-abc.vercel.app",
      },
    },
    res,
    ["https://license-server-flame-eta.vercel.app"]
  );
  assert.equal(allowed, true);
  assert.equal(
    res.headers["Access-Control-Allow-Origin"],
    "https://preview-abc.vercel.app"
  );
});

test("다른 출처의 관리자 요청은 허용하지 않는다", () => {
  const res = fakeResponse();
  const allowed = security.applyCors(
    {
      headers: {
        origin: "https://evil.example",
        host: "preview-abc.vercel.app",
      },
    },
    res,
    ["https://license-server-flame-eta.vercel.app"]
  );
  assert.equal(allowed, false);
  assert.equal(res.headers["Access-Control-Allow-Origin"], undefined);
});
