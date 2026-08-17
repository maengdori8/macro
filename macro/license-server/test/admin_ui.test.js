const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const adminPath = path.join(__dirname, "..", "admin.html");
const html = fs.readFileSync(adminPath, "utf8");

test("관리자 UI는 중복 ID 없이 실행 가능한 스크립트를 포함한다", () => {
  const ids = [...html.matchAll(/\sid="([^"]+)"/g)].map((match) => match[1]);
  assert.equal(new Set(ids).size, ids.length);

  const scripts = [...html.matchAll(/<script>([\s\S]*?)<\/script>/g)];
  assert.equal(scripts.length, 1);
  assert.doesNotThrow(() => new vm.Script(scripts[0][1]));
});

test("이미 발급된 키를 빠르게 다시 찾고 복사하는 UI가 제공된다", () => {
  for (const requiredId of [
    "recentKeys",
    "copyFilteredButton",
    "statusTabs",
    "batchSummary",
    "licenseTable",
  ]) {
    assert.match(html, new RegExp(`id="${requiredId}"`));
  }
  assert.match(html, /최근 발급된 키/);
  assert.match(html, /현재 목록 키 복사/);
  assert.match(html, /대기 키 \$\{b\.pending\}개 복사/);
  assert.match(html, /getFilteredLicenses\(\)/);
});

test("수동 발급·자판기용 키·사용 중인 키가 분리된다", () => {
  assert.match(html, /data-mode="manual">수동 발급/);
  assert.match(html, /data-mode="vending">자판기용/);
  assert.match(html, /data-license-scope="active"/);
  assert.match(html, /data-scope="vending">자판기용 키/);
  assert.match(html, /data-scope="manual">수동 발급/);
  assert.match(html, /data-scope="active">사용 중/);
  assert.match(html, /issueMode === "manual" \? 1/);
});

test("수동·자판기 발급 모두 1~24시간권을 선택할 수 있다", () => {
  assert.match(html, /id="termUnitSwitch"/);
  assert.match(html, /data-term-unit="hour"/);
  assert.match(html, /data-unit="hour" data-value="24"/);
  assert.match(html, /issueUnit === "hour" \? \{ hours:licenseTerm\.hours \}/);
  assert.match(html, /item\.term/);
});

test("제품별로 재고를 나눠 볼 수 있다", () => {
  assert.match(html, /id="productTabs"/);
  for (const product of ["all", "macro", "macro_pro", "mpause"]) {
    assert.match(html, new RegExp(`data-product="${product}"`));
  }
  assert.match(html, /<option value="macro_pro">/);
  // 목록·최근 키·배치 요약·통계가 전부 같은 필터를 본다. 하나라도 빠지면
  // "보이는 재고 수"와 "실제로 복사되는 키"가 어긋난다.
  assert.match(html, /matchesProductScope\(item\) && \(!batch/);
  assert.match(html, /matchesLicenseScope\(item\) && matchesProductScope\(item\)/);
  assert.match(html, /const scoped = scopedLicenses\(\);/);
  assert.match(html, /for \(const item of scopedLicenses\(\)\)/);
});

test("상태 탭과 제품 탭은 서로의 선택 표시를 지우지 않는다", () => {
  // 둘 다 .status-tab 클래스를 쓴다. 선택 표시를 갱신할 때 전체를 훑으면
  // 한쪽을 누르는 순간 다른 쪽 표시가 사라져, 어떤 재고를 보고 있는지 알 수 없다.
  assert.match(html, /querySelectorAll\("#statusTabs \.status-tab"\)/);
  assert.match(html, /querySelectorAll\("#productTabs \.status-tab"\)/);
  assert.doesNotMatch(html, /querySelectorAll\("\.status-tab"\)/);
});

test("배치 대기 키 복사는 제품이 섞이지 않는다", () => {
  // batchKey 는 batchId 가 없으면 배치명으로 떨어진다. 단건 발급은 배치명이
  // 전부 같아서, 제품을 안 보면 다른 앱 키까지 한 덩어리로 복사된다.
  assert.match(
    html,
    /batchKey\(license\) === id && license\.status === "pending" && matchesProductScope\(license\)/
  );
});

test("제품 필터 판정은 값이 없는 옛 키를 mAuto 로 본다", () => {
  const source = html.match(/const licenseProduct = [\s\S]*?\n    \}/)[0];
  const context = vm.createContext({});
  vm.runInContext(`let productScope = "all";\n${source}`, context);

  // let 으로 만든 바인딩은 컨텍스트 객체의 속성이 아니다(globalThis 에 안 붙는다).
  // 그래서 값을 바꿀 때도 컨텍스트 **안에서** 대입해야 함수가 새 값을 본다.
  const check = (scope, item) => vm.runInContext(
    `productScope = ${JSON.stringify(scope)}; matchesProductScope(${JSON.stringify(item)})`,
    context
  );

  assert.equal(check("all", { product: "mpause" }), true);
  assert.equal(check("macro", {}), true, "product 없는 기존 키는 mAuto 재고다");
  assert.equal(check("macro", { product: "macro_pro" }), false, "프로는 일반 재고가 아니다");
  assert.equal(check("macro_pro", { product: "macro_pro" }), true);
  assert.equal(check("mpause", { product: "macro" }), false);
});

test("좁은 화면에서는 라이선스 표가 카드 목록으로 전환된다", () => {
  assert.match(html, /@media \(max-width:900px\)/);
  assert.match(html, /table,tbody \{ display:block; min-width:0; \}/);
  assert.match(html, /tbody tr \{ display:grid; grid-template-columns:1fr 1fr;/);
  assert.match(html, /content:attr\(data-label\)/);
});
