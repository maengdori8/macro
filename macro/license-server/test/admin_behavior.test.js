"use strict";

// 관리자 패널의 **동작**을 실제로 돌려서 검증한다.
//
// 왜 필요한가: admin_ui.test.js 는 admin.html 을 문자열로 훑는다. 그것만으로는
// "코드가 거기 적혀 있다"까지만 보증되고 "그게 실제로 돈다"는 못 본다. 적대적
// 리뷰에서 실측으로 확인된 사실 — 제품 탭 클릭 리스너를 통째로 지워도, 필터
// 함수 본문을 `return licenses;` 로 되돌려도 문자열 테스트는 전부 통과했다.
// 즉 재고 혼입을 막으려고 넣은 장치가 죽어 있어도 초록불이 켜졌다.
//
// 그래서 여기서는 <script> 본문을 최소 DOM 스텁과 함께 vm 으로 실행하고,
// 데이터를 넣은 뒤 화면에 실제로 찍힌 값을 확인한다.

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const html = fs.readFileSync(path.join(__dirname, "..", "admin.html"), "utf8");
const script = html.match(/<script>([\s\S]*?)<\/script>/)[1];

// ─── 최소 DOM 스텁 ─────────────────────────────────────────────────────────
// 패널이 실제로 쓰는 것만 흉내 낸다. 스크립트가 새 DOM API 를 쓰기 시작하면
// 여기서 터지므로, 그때 스텁을 늘리면 된다(조용히 통과하지 않는다).

function makeElement(id = "") {
  const classes = new Set();
  return {
    id,
    value: "",
    textContent: "",
    innerHTML: "",
    disabled: false,
    max: "",
    style: {},
    dataset: {},
    listeners: {},
    classList: {
      add: (name) => classes.add(name),
      remove: (name) => classes.delete(name),
      contains: (name) => classes.has(name),
      toggle: (name, force) => (force ? classes.add(name) : classes.delete(name)),
    },
    addEventListener(type, handler) {
      (this.listeners[type] = this.listeners[type] || []).push(handler);
    },
    dispatch(type, event = {}) {
      for (const handler of this.listeners[type] || []) handler(event);
    },
    closest() {
      return null;
    },
    scrollIntoView() {},
    focus() {},
    select() {},
    remove() {},
    appendChild() {},
    querySelectorAll() {
      return [];
    },
  };
}

function makeContext() {
  const elements = new Map();
  const byId = (id) => {
    if (!elements.has(id)) elements.set(id, makeElement(id));
    return elements.get(id);
  };
  // 제품 탭 네 개는 실제로 존재해야 선택 표시 갱신 경로가 돈다.
  const productTabs = ["all", "macro", "macro_pro", "mpause"].map((product) => {
    const tab = makeElement(`tab-${product}`);
    tab.dataset.product = product;
    return tab;
  });
  const statusTabs = ["all", "vending", "manual", "active", "ended"].map((scope) => {
    const tab = makeElement(`tab-scope-${scope}`);
    tab.dataset.scope = scope;
    return tab;
  });

  const document = {
    getElementById: byId,
    addEventListener() {},
    createElement: () => makeElement(),
    body: { appendChild() {} },
    execCommand() {},
    querySelectorAll(selector) {
      if (selector === "#productTabs .status-tab") return productTabs;
      if (selector === "#statusTabs .status-tab") return statusTabs;
      return [];
    },
  };

  const context = vm.createContext({
    document,
    console,
    JSON,
    Math,
    Date,
    Set,
    Map,
    Number,
    String,
    Object,
    Array,
    Boolean,
    Error,
    isNaN,
    parseInt,
    parseFloat,
    encodeURIComponent,
    setTimeout: () => 0,
    clearTimeout: () => {},
    crypto: { randomUUID: () => "11111111222233334444555566667777" },
    navigator: { clipboard: { writeText: async () => {} } },
    fetch: async () => ({ ok: true, json: async () => ({ licenses: [] }) }),
    URL: { createObjectURL: () => "blob:x", revokeObjectURL: () => {} },
    Blob: function Blob() {},
    location: { hash: "" },
  });
  vm.runInContext(script, context);
  return { context, byId, productTabs };
}

const license = (over = {}) => ({
  key: "ABCDE-FGHJK-LMNPQ-RSTUV-WXYZ2",
  status: "pending",
  days: 30,
  term: "30일권",
  hwids: [],
  maxHwids: 3,
  batchName: "수동 발급",
  issuedAt: 1,
  ...over,
});

function load(context, licenses) {
  vm.runInContext(`licenses = ${JSON.stringify(licenses)}; renderAll();`, context);
}

// ─── 검증 ──────────────────────────────────────────────────────────────────

test("제품 탭을 누르면 재고 수가 그 제품 기준으로 바뀐다", () => {
  const { context, byId, productTabs } = makeContext();
  load(context, [
    license({ key: "AAAAA-AAAAA-AAAAA-AAAAA-AAAAA", product: "macro" }),
    license({ key: "BBBBB-BBBBB-BBBBB-BBBBB-BBBBB", product: "macro" }),
    license({ key: "CCCCC-CCCCC-CCCCC-CCCCC-CCCCC", product: "mpause" }),
    license({ key: "DDDDD-DDDDD-DDDDD-DDDDD-DDDDD", product: "macro_pro" }),
  ]);

  assert.equal(byId("inventoryPending").textContent, 4, "전체 제품 기준이 아니다");

  const click = (product) =>
    productTabs
      .find((tab) => tab.dataset.product === product)
      .dispatch("click", { target: { closest: () => ({ dataset: { product } }) } });

  vm.runInContext(`setProductScope("mpause")`, context);
  assert.equal(byId("inventoryPending").textContent, 1, "제품 필터가 재고에 안 걸린다");
  assert.equal(byId("inventoryScopeText").textContent, "mPause 기준");

  vm.runInContext(`setProductScope("macro")`, context);
  assert.equal(byId("inventoryPending").textContent, 2);

  vm.runInContext(`setProductScope("macro_pro")`, context);
  assert.equal(byId("inventoryPending").textContent, 1, "프로가 일반 재고에 섞였다");

  vm.runInContext(`setProductScope("all")`, context);
  assert.equal(byId("inventoryPending").textContent, 4);
  void click;
});

test("제품 탭 클릭이 실제로 필터를 바꾼다(리스너가 살아 있다)", () => {
  const { context, byId } = makeContext();
  load(context, [
    license({ key: "AAAAA-AAAAA-AAAAA-AAAAA-AAAAA", product: "macro" }),
    license({ key: "CCCCC-CCCCC-CCCCC-CCCCC-CCCCC", product: "mpause" }),
  ]);
  // 패널이 등록한 #productTabs 클릭 핸들러를 직접 깨운다.
  byId("productTabs").dispatch("click", {
    target: { closest: () => ({ dataset: { product: "mpause" } }) },
  });
  assert.equal(byId("inventoryPending").textContent, 1, "탭 클릭이 아무 일도 안 한다");
});

test("목록·모수도 같은 제품 기준을 쓴다", () => {
  const { context, byId } = makeContext();
  load(context, [
    license({ key: "AAAAA-AAAAA-AAAAA-AAAAA-AAAAA", product: "macro" }),
    license({ key: "BBBBB-BBBBB-BBBBB-BBBBB-BBBBB", product: "macro" }),
    license({ key: "CCCCC-CCCCC-CCCCC-CCCCC-CCCCC", product: "mpause" }),
  ]);
  vm.runInContext(`setProductScope("mpause")`, context);
  const shown = vm.runInContext("getFilteredLicenses().length", context);
  assert.equal(shown, 1);
  // 한 화면에 서로 다른 '전체'가 두 개 뜨면 어느 게 재고인지 알 수 없다.
  assert.equal(byId("resultCount").textContent, "1개 표시 / 전체 1개");
  assert.equal(byId("statTotal").textContent, 1);
});

test("초기화는 제품 탭까지 되돌린다", () => {
  const { context, byId } = makeContext();
  load(context, [
    license({ key: "AAAAA-AAAAA-AAAAA-AAAAA-AAAAA", product: "macro" }),
    license({ key: "CCCCC-CCCCC-CCCCC-CCCCC-CCCCC", product: "mpause" }),
  ]);
  vm.runInContext(`setProductScope("mpause")`, context);
  byId("clearFilterButton").dispatch("click", {});
  assert.equal(vm.runInContext("productScope", context), "all");
  assert.equal(byId("inventoryPending").textContent, 2, "초기화했는데 한 제품만 남았다");
});

test("배치 대기 키 복사는 같은 배치라도 제품이 다르면 섞지 않는다", async () => {
  const { context, byId } = makeContext();
  // batchId 없는 단건 발급들 — batchKey 가 배치명("수동 발급")으로 뭉친다.
  load(context, [
    license({ key: "AAAAA-AAAAA-AAAAA-AAAAA-AAAAA", product: "macro" }),
    license({ key: "CCCCC-CCCCC-CCCCC-CCCCC-CCCCC", product: "mpause" }),
  ]);
  const copied = [];
  vm.runInContext(
    `copyText = async (text) => { copiedForTest = text; };
     copiedForTest = "";`,
    context
  );
  vm.runInContext(`setProductScope("mpause")`, context);
  await byId("batchSummary").dispatch("click", {
    target: {
      closest: (selector) =>
        selector === ".batch-action"
          ? { dataset: { action: "copy" } }
          : { dataset: { batchFilter: "수동 발급" } },
    },
  });
  await new Promise((resolve) => setTimeout(resolve, 0));
  copied.push(vm.runInContext("copiedForTest", context));
  assert.equal(copied[0], "CCCCC-CCCCC-CCCCC-CCCCC-CCCCC", "다른 제품 키가 섞여 복사됐다");
});

test("클라이언트가 없는 제품을 고르면 판매 금지 경고가 뜬다", () => {
  const { context, byId } = makeContext();
  byId("productInput").value = "macro_pro";
  vm.runInContext("updateIssuePreview();", context);
  assert.match(byId("productWarning").textContent, /배포 전|일반으로 동작/);

  byId("productInput").value = "mpause";
  vm.runInContext("updateIssuePreview();", context);
  assert.equal(byId("productWarning").textContent, "", "정상 제품에 경고가 남아 있다");
});

test("발급 결과 파일명과 메타에 제품이 들어간다", () => {
  const { context, byId } = makeContext();
  const downloads = [];
  vm.runInContext(
    `lastBatch = { product:"mpause", plainText:"AAAAA-AAAAA-AAAAA-AAAAA-AAAAA",
       count:1, days:30, hours:0, term:"30일권", batchName:"7월 재고" };
     showBatchResult();`,
    context
  );
  assert.match(byId("resultMeta").textContent, /mPause/, "붙여넣기 직전 화면에 제품이 없다");
  assert.match(byId("resultTitle").textContent, /mPause/);

  // 다운로드 파일명은 제품에서 만들어져야 한다(내용이 mPause 인데 이름이 mauto 면 오표기).
  const anchor = makeElement();
  vm.runInContext(`downloadResultForTest = downloadResult;`, context);
  context.document.createElement = () => anchor;
  anchor.click = () => downloads.push(anchor.download);
  vm.runInContext(`downloadResultForTest();`, context);
  assert.equal(downloads.length, 1);
  assert.match(downloads[0], /^mpause-30d-/, `파일명이 제품과 다르다: ${downloads[0]}`);
});

// ─── 프로 운영 설정 패널 ───────────────────────────────────────────────────

test("프로 설정 저장이 % 입력을 비율로 바꿔 setProSettings 로 보낸다", async () => {
  const { context, byId } = makeContext();
  const calls = [];
  context.fetch = async (url, opts) => {
    calls.push({ url, opts });
    return {
      ok: true,
      json: async () => ({ success: true, message: "저장됨", proSettings: { autoExitRatio: 0.35 } }),
    };
  };
  byId("autoExitRatioInput").value = "35";
  await vm.runInContext("saveProSettings()", context);

  assert.equal(calls.length, 1, "저장 요청이 나가지 않았다");
  assert.equal(calls[0].opts.method, "PATCH");
  const body = JSON.parse(calls[0].opts.body);
  assert.equal(body.action, "setProSettings");
  assert.equal(body.autoExitRatio, 0.35, "% 가 비율(0~1)로 변환되지 않았다");
  // 저장 후에는 서버가 확정한 값으로 입력칸을 되돌린다.
  assert.equal(byId("autoExitRatioInput").value, 35);
});

test("빈 입력·범위 밖 입력은 저장 요청 자체를 보내지 않는다", async () => {
  const { context, byId } = makeContext();
  const calls = [];
  context.fetch = async (...args) => {
    calls.push(args);
    return { ok: true, json: async () => ({ success: true }) };
  };
  // Number("") === 0 이라, 빈 입력을 그냥 넘기면 '0% 저장'이 조용히 나간다.
  for (const bad of ["", "abc", "-5", "120"]) {
    byId("autoExitRatioInput").value = bad;
    await vm.runInContext("saveProSettings()", context);
  }
  assert.equal(calls.length, 0, "잘못된 입력이 저장 요청으로 나갔다");
});

test("목록 동기화가 프로 설정 패널을 채운다", async () => {
  const { context, byId } = makeContext();
  context.fetch = async () => ({
    ok: true,
    json: async () => ({ success: true, licenses: [], proSettings: { autoExitRatio: 0.6 } }),
  });
  await vm.runInContext("loadLicenses()", context);
  assert.equal(byId("autoExitRatioInput").value, 60);

  // 설정이 안 실려 와도(구버전 서버·읽기 실패) 목록 동기화는 죽지 않는다.
  context.fetch = async () => ({ ok: true, json: async () => ({ success: true, licenses: [] }) });
  await vm.runInContext("loadLicenses()", context);
  assert.equal(byId("autoExitRatioInput").value, 60, "값 없는 응답이 입력칸을 지웠다");
});

// ─── 키별 2점차 자동 종료 비율 ─────────────────────────────────────────────

test("프로 키 행에는 키별 비율(없으면 전역값)과 '종료율' 버튼이 뜨고, 일반 키 행에는 없다", async () => {
  const { context, byId } = makeContext();
  // 전역값을 먼저 알아야 '전역 40%' 문구가 나온다(목록 동기화가 채운다).
  context.fetch = async () => ({
    ok: true,
    json: async () => ({
      success: true,
      proSettings: { autoExitRatio: 0.4 },
      licenses: [
        license({ key: "PRO11-PRO11-PRO11-PRO11-PRO11", product: "macro_pro", autoExitRatio: 0.6 }),
        license({ key: "PRO22-PRO22-PRO22-PRO22-PRO22", product: "macro_pro", autoExitRatio: null }),
        license({ key: "BASIC-BASIC-BASIC-BASIC-BASIC", product: "macro", autoExitRatio: 0.2 }),
      ],
    }),
  });
  await vm.runInContext("loadLicenses()", context);
  const html = byId("licenseTable").innerHTML;

  assert.match(html, /종료 60% \(키별\)/, "키별 비율이 표시되지 않았다");
  assert.match(html, /종료 40% \(전역\)/, "키별 값 없는 프로 키가 전역값을 보여 주지 않았다");
  // 일반 키는 문서에 값이 적혀 있어도 표시하지 않는다(기능이 없는 제품이다).
  assert.equal((html.match(/data-action="exitsettings"/g) || []).length, 2, "종료 설정 버튼은 프로 키 두 개에만 있어야 한다");
  assert.equal(html.includes('data-action="exitsettings" data-key="BASIC-BASIC-BASIC-BASIC-BASIC"'), false);
  assert.equal(html.includes("종료 20%"), false, "일반 키 행에 비율이 새어 나왔다");
});

test("종료율 입력은 % → 비율로, 빈 값은 해제(null)로 보내고, 범위 밖은 요청 자체를 막는다", () => {
  const { context } = makeContext();
  // vm 컨텍스트의 객체는 프로토타입이 달라 deepStrictEqual 이 거짓 실패한다 → 평문으로 비교.
  const build = (raw) =>
    JSON.parse(JSON.stringify(vm.runInContext(`buildExitRatioPatch("K", ${JSON.stringify(raw)})`, context)));
  assert.deepEqual(build("60"), { key: "K", autoExitRatio: 0.6 });
  assert.deepEqual(build(" 0 "), { key: "K", autoExitRatio: 0 });
  assert.deepEqual(build("100"), { key: "K", autoExitRatio: 1 });
  assert.deepEqual(build(""), { key: "K", autoExitRatio: null });
  assert.deepEqual(build("   "), { key: "K", autoExitRatio: null });
  for (const bad of ["abc", "-5", "120", "1e9"]) {
    assert.throws(() => build(bad), /0~100/, `통과되면 안 되는 입력: ${bad}`);
  }
});

test("'종료율' 행 동작이 프롬프트 값을 PATCH 로 보낸다(리스너·배선이 살아 있다)", async () => {
  const { context } = makeContext();
  const key = "PRO11-PRO11-PRO11-PRO11-PRO11";
  const rows = [license({ key, product: "macro_pro", autoExitRatio: null })];
  const calls = [];
  // 행 동작은 처리 뒤 목록을 다시 불러온다 — 그때도 같은 키가 있어야 다음 동작이 돈다.
  context.fetch = async (url, opts) => {
    calls.push({ url, opts });
    return { ok: true, json: async () => ({ success: true, licenses: rows }) };
  };
  load(context, rows);
  context.__event = { target: { closest: () => ({ dataset: { key, action: "exitratio" } }) } };
  const patches = () => calls.filter((c) => c.opts && c.opts.method === "PATCH");

  context.prompt = () => "60";
  await vm.runInContext("handleRowAction(__event)", context);
  assert.equal(patches().length, 1, "PATCH 요청이 나가지 않았다");
  assert.deepEqual(JSON.parse(patches()[0].opts.body), { key, autoExitRatio: 0.6 });

  // 빈 값 = 해제.
  calls.length = 0;
  context.prompt = () => "";
  await vm.runInContext("handleRowAction(__event)", context);
  assert.deepEqual(JSON.parse(patches()[0].opts.body), { key, autoExitRatio: null });

  // 취소(null) 와 범위 밖은 요청이 없다.
  for (const answer of [null, "abc", "150"]) {
    calls.length = 0;
    context.prompt = () => answer;
    await vm.runInContext("handleRowAction(__event)", context);
    assert.equal(patches().length, 0, `요청이 나가면 안 되는 입력: ${answer}`);
  }
});


// ─── 키별 종료 설정 모달 + 전역 다중 필드 ─────────────────────────────────

const plain = (value) => JSON.parse(JSON.stringify(value));

function loadWithSettings(context, licenses, proSettings) {
  context.fetch = async () => ({ ok: true, json: async () => ({ success: true, licenses, proSettings }) });
  return vm.runInContext("loadLicenses()", context);
}

test("전역 패널 저장은 채운 필드만 보내고, 후반 비율 빈 칸은 null(해제)로 보낸다", async () => {
  const { context, byId } = makeContext();
  const calls = [];
  context.fetch = async (url, opts) => {
    calls.push({ url, opts });
    return { ok: true, json: async () => ({ success: true, message: "ok", proSettings: { autoExitRatio: 0.5, autoExitBaseDeficit: 2, autoExitHardDeficit: 4, autoExitLateMinute: 70, autoExitLateDeficit: 1, autoExitLateRatio: null } }) };
  };
  byId("autoExitRatioInput").value = "50";
  byId("autoExitHardDeficitInput").value = "4";
  byId("autoExitLateRatioInput").value = "";
  await vm.runInContext("saveProSettings()", context);
  const body = JSON.parse(calls[0].opts.body);
  assert.deepEqual(body, { action: "setProSettings", autoExitRatio: 0.5, autoExitHardDeficit: 4, autoExitLateRatio: null });
  // 응답의 실효값으로 입력칸이 채워진다.
  assert.equal(byId("autoExitHardDeficitInput").value, 4);
  assert.equal(byId("autoExitLateMinuteInput").value, 70);
  assert.equal(byId("autoExitLateRatioInput").value, "");

  // 범위 밖 정수(소수·초과)는 요청 자체가 안 나간다.
  for (const [id, bad] of [["autoExitHardDeficitInput", "10"], ["autoExitLateMinuteInput", "70.5"], ["autoExitBaseDeficitInput", "0"]]) {
    calls.length = 0;
    byId(id).value = bad;
    await vm.runInContext("saveProSettings()", context);
    assert.equal(calls.length, 0, `요청이 나가면 안 되는 입력: ${id}=${bad}`);
    byId(id).value = "";
  }
});

test("'종료 설정' 행 동작이 모달을 열고 키별 값·전역 안내를 채운다", async () => {
  const { context, byId } = makeContext();
  const key = "PRO11-PRO11-PRO11-PRO11-PRO11";
  await loadWithSettings(
    context,
    [license({ key, product: "macro_pro", autoExitRatio: 0.6, exitSettings: { autoExitRatio: 0.6, autoExitBaseDeficit: null, autoExitHardDeficit: 4, autoExitLateMinute: null, autoExitLateDeficit: null, autoExitLateRatio: null } })],
    { autoExitRatio: 0.4, autoExitBaseDeficit: 2, autoExitHardDeficit: 3, autoExitLateMinute: 70, autoExitLateDeficit: 1, autoExitLateRatio: null }
  );
  context.__event = { target: { closest: () => ({ dataset: { key, action: "exitsettings" } }) } };
  await vm.runInContext("handleRowAction(__event)", context);
  assert.equal(byId("exitSettingsModal").classList.contains("show"), true, "모달이 안 열렸다");
  assert.equal(byId("esRatioInput").value, 60);
  assert.equal(byId("esHardDeficitInput").value, 4);
  assert.equal(byId("esLateMinuteInput").value, "");
  assert.equal(byId("esLateMinuteInput").placeholder, "전역 70");
  assert.equal(byId("esLateRatioInput").placeholder, "기본 비율");
  assert.match(byId("exitSettingsMeta").textContent, new RegExp(key));
});

test("모달 저장은 여섯 필드를 전부(빈 칸=null) 보내고, 전부 해제는 모두 null 을 보낸다", async () => {
  const { context, byId } = makeContext();
  const key = "PRO11-PRO11-PRO11-PRO11-PRO11";
  const rows = [license({ key, product: "macro_pro", exitSettings: {} })];
  const calls = [];
  context.fetch = async (url, opts) => {
    calls.push({ url, opts });
    return { ok: true, json: async () => ({ success: true, message: "ok", licenses: rows, proSettings: { autoExitRatio: 0.4 } }) };
  };
  await vm.runInContext("loadLicenses()", context);
  vm.runInContext(`openExitSettings(${JSON.stringify(key)})`, context);
  byId("esRatioInput").value = "55";
  byId("esHardDeficitInput").value = "4";
  byId("esLateMinuteInput").value = "75";
  byId("esLateRatioInput").value = "100";
  await vm.runInContext("saveExitSettings(false)", context);
  const saved = calls.filter((c) => c.opts && c.opts.method === "PATCH");
  assert.equal(saved.length, 1);
  assert.deepEqual(plain(JSON.parse(saved[0].opts.body)), {
    key,
    exitSettings: { autoExitRatio: 0.55, autoExitBaseDeficit: null, autoExitHardDeficit: 4, autoExitLateMinute: 75, autoExitLateDeficit: null, autoExitLateRatio: 1 },
  });
  assert.equal(byId("exitSettingsModal").classList.contains("show"), false, "저장 뒤 모달이 닫혀야 한다");

  // 범위 밖은 요청이 안 나간다.
  calls.length = 0;
  vm.runInContext(`openExitSettings(${JSON.stringify(key)})`, context);
  byId("esHardDeficitInput").value = "12";
  await assert.rejects(() => vm.runInContext("saveExitSettings(false)", context), /0~9/);
  assert.equal(calls.filter((c) => c.opts && c.opts.method === "PATCH").length, 0);

  // 전부 해제.
  calls.length = 0;
  await vm.runInContext("saveExitSettings(true)", context);
  const cleared = calls.filter((c) => c.opts && c.opts.method === "PATCH");
  assert.equal(cleared.length, 1);
  const body = plain(JSON.parse(cleared[0].opts.body));
  assert.equal(Object.keys(body.exitSettings).length, 6);
  assert.ok(Object.values(body.exitSettings).every((v) => v === null));
});

test("키별 규칙이 잡힌 행은 '맞춤 규칙' 표시가 붙는다", async () => {
  const { context, byId } = makeContext();
  await loadWithSettings(
    context,
    [
      license({ key: "PRO11-PRO11-PRO11-PRO11-PRO11", product: "macro_pro", autoExitRatio: null, exitSettings: { autoExitHardDeficit: 4 } }),
      license({ key: "PRO22-PRO22-PRO22-PRO22-PRO22", product: "macro_pro", autoExitRatio: null, exitSettings: {} }),
    ],
    { autoExitRatio: 0.4 }
  );
  const html = byId("licenseTable").innerHTML;
  assert.match(html, /종료 40% \(전역\) · 맞춤 규칙/);
  assert.equal((html.match(/맞춤 규칙/g) || []).length, 1);
});
