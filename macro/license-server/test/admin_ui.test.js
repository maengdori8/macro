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

test("좁은 화면에서는 라이선스 표가 카드 목록으로 전환된다", () => {
  assert.match(html, /@media \(max-width:900px\)/);
  assert.match(html, /table,tbody \{ display:block; min-width:0; \}/);
  assert.match(html, /tbody tr \{ display:grid; grid-template-columns:1fr 1fr;/);
  assert.match(html, /content:attr\(data-label\)/);
});
