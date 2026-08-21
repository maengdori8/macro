"use strict";

// 프로 운영 설정(0:2 자동 종료 비율) — 저장·전파 규칙을 고정한다.
//
// 지켜야 할 것:
//   1) 범위 밖·형식 불량 값은 저장도 전파도 안 된다(클라이언트가 조용히 기본값으로
//      돌아가 "조절이 안 된다"로 보이는 사고 방지)
//   2) 문서가 없으면 기본값 40% — 배포 직후·롤백 후에도 프로가 그대로 돈다
//   3) 비율은 macro_pro 문서에만 실린다(일반·mPause 는 기능 자체가 없다)
//   4) 읽기는 캐시된다(verify 마다 Firestore 를 읽어 할당량을 잠식하지 않게)

const assert = require("node:assert/strict");
const test = require("node:test");
const {
  DEFAULT_AUTO_EXIT_RATIO,
  autoExitRatioAppliesTo,
  normalizeAutoExitRatio,
  readAutoExitRatio,
  resolveAutoExitRatio,
  writeAutoExitRatio,
} = require("../lib/pro_settings");
const { FakeFirestore } = require("./fake_firestore");

// ─── 값 검증 ───────────────────────────────────────────────────────────────

test("0~1 사이 숫자만 통과한다", () => {
  assert.equal(normalizeAutoExitRatio(0), 0);
  assert.equal(normalizeAutoExitRatio(0.4), 0.4);
  assert.equal(normalizeAutoExitRatio(1), 1);
  assert.equal(normalizeAutoExitRatio("0.35"), 0.35);
});

test("범위 밖·형식 불량은 전부 null 이다", () => {
  for (const bad of [-0.1, 1.5, NaN, Infinity, -Infinity, "", " ", "abc", null, undefined, true, false, {}, []]) {
    assert.equal(normalizeAutoExitRatio(bad), null, `통과되면 안 되는 값: ${String(bad)}`);
  }
});

test("비율은 macro_pro 에만 적용된다", () => {
  assert.equal(autoExitRatioAppliesTo("macro_pro"), true);
  assert.equal(autoExitRatioAppliesTo("macro"), false);
  assert.equal(autoExitRatioAppliesTo("mpause"), false);
  assert.equal(autoExitRatioAppliesTo(""), false);
  assert.equal(autoExitRatioAppliesTo(undefined), false);
});

// ─── 읽기 ──────────────────────────────────────────────────────────────────

test("문서가 없으면 기본값 40% 다", async () => {
  const db = new FakeFirestore();
  assert.equal(await readAutoExitRatio(db), DEFAULT_AUTO_EXIT_RATIO);
});

test("저장된 값이 깨져 있어도 기본값으로 돌아간다", async () => {
  const db = new FakeFirestore({ "config/pro_settings": { autoExitRatio: 7 } });
  assert.equal(await readAutoExitRatio(db), DEFAULT_AUTO_EXIT_RATIO);
});

test("읽기는 TTL 캐시를 쓴다 — verify 마다 Firestore 를 읽지 않는다", async () => {
  const db = new FakeFirestore({ "config/pro_settings": { autoExitRatio: 0.2 } });
  const t0 = 1_000_000;
  assert.equal(await readAutoExitRatio(db, t0), 0.2);

  // 문서를 몰래 바꿔도 TTL 안에서는 캐시된 값이 나온다(= 재읽기하지 않았다는 증거).
  db._docs.set("config/pro_settings", { autoExitRatio: 0.9 });
  assert.equal(await readAutoExitRatio(db, t0 + 1000), 0.2);

  // TTL 이 지나면 새 값을 읽는다.
  assert.equal(await readAutoExitRatio(db, t0 + 61 * 1000), 0.9);
});

// ─── 키별 비율 → 전역 폴백 ─────────────────────────────────────────────────

test("라이센스 문서에 키별 비율이 있으면 그것이 전역값을 이긴다", async () => {
  const db = new FakeFirestore({ "config/pro_settings": { autoExitRatio: 0.7 } });
  assert.equal(await resolveAutoExitRatio(db, { autoExitRatio: 0.25 }), 0.25);
  assert.equal(await resolveAutoExitRatio(db, { autoExitRatio: 0 }), 0, "0% 도 유효한 키별 값이다");
  assert.equal(await resolveAutoExitRatio(db, { autoExitRatio: "0.1" }), 0.1);
});

test("키별 값이 없거나(null) 깨졌으면 전역값으로 떨어진다", async () => {
  const db = new FakeFirestore({ "config/pro_settings": { autoExitRatio: 0.7 } });
  for (const missing of [undefined, null, 7, -1, "abc", "", NaN]) {
    assert.equal(
      await resolveAutoExitRatio(db, { autoExitRatio: missing }),
      0.7,
      `전역으로 안 떨어짐: ${String(missing)}`
    );
  }
  assert.equal(await resolveAutoExitRatio(db, {}), 0.7);
  assert.equal(await resolveAutoExitRatio(db, null), 0.7);
});

test("전역 문서도 없으면 내장 기본값이다", async () => {
  const db = new FakeFirestore();
  assert.equal(await resolveAutoExitRatio(db, { autoExitRatio: null }), DEFAULT_AUTO_EXIT_RATIO);
});

// ─── 쓰기 ──────────────────────────────────────────────────────────────────

test("저장하면 같은 인스턴스가 즉시 새 값을 읽는다(캐시 무효화)", async () => {
  const db = new FakeFirestore();
  const t0 = 1_000_000;
  assert.equal(await readAutoExitRatio(db, t0), DEFAULT_AUTO_EXIT_RATIO);

  assert.equal(await writeAutoExitRatio(db, 0.25, t0), 0.25);
  assert.equal(db.read("config/pro_settings").autoExitRatio, 0.25);
  assert.equal(db.read("config/pro_settings").updatedAt, t0);
  // TTL 이 안 지났어도 쓰기 직후에는 새 값이다.
  assert.equal(await readAutoExitRatio(db, t0 + 1000), 0.25);
});

test("잘못된 값은 저장 자체가 거부된다", async () => {
  const db = new FakeFirestore();
  for (const bad of [1.5, -0.1, NaN, "abc", null]) {
    await assert.rejects(() => writeAutoExitRatio(db, bad), /0~1/);
  }
  assert.equal(db.read("config/pro_settings"), undefined, "거부된 값이 저장됐다");
});

test("merge 저장이라 미래에 추가될 다른 설정 필드를 지우지 않는다", async () => {
  const db = new FakeFirestore({ "config/pro_settings": { autoExitRatio: 0.4, futureField: "keep" } });
  await writeAutoExitRatio(db, 0.5);
  assert.equal(db.read("config/pro_settings").futureField, "keep");
  assert.equal(db.read("config/pro_settings").autoExitRatio, 0.5);
});


// ─── 규칙 전부(점차·후반 분·후반 비율) — 키별 → 전역 → 기본값 ───────────────

const {
  FIELD_SPECS,
  SETTING_FIELDS,
  effectiveSettings,
  normalizeSetting,
  normalizeSettings,
  readProSettings,
  resolveExitSettings,
  toClientPayload,
  writeProSettings,
} = require("../lib/pro_settings");

test("정수 필드는 범위 안 정수만, 비율 필드는 0~1 만 통과한다", () => {
  assert.equal(normalizeSetting("autoExitBaseDeficit", 2), 2);
  assert.equal(normalizeSetting("autoExitBaseDeficit", "3"), 3);
  assert.equal(normalizeSetting("autoExitBaseDeficit", 0), null);
  assert.equal(normalizeSetting("autoExitHardDeficit", 0), 0, "0 = 끔은 유효하다");
  assert.equal(normalizeSetting("autoExitHardDeficit", 10), null);
  assert.equal(normalizeSetting("autoExitLateMinute", 120), 120);
  assert.equal(normalizeSetting("autoExitLateMinute", 121), null);
  assert.equal(normalizeSetting("autoExitLateMinute", 70.5), null);
  assert.equal(normalizeSetting("autoExitLateRatio", 1), 1);
  assert.equal(normalizeSetting("autoExitLateRatio", 1.5), null);
  assert.equal(normalizeSetting("nope", 1), null);
  for (const bad of [true, false, null, undefined, "", " ", NaN, {}, []]) {
    assert.equal(normalizeSetting("autoExitBaseDeficit", bad), null, `통과되면 안 됨: ${String(bad)}`);
  }
});

test("normalizeSettings 는 모든 필드를 돌려주고 없는 건 null 이다", () => {
  const out = normalizeSettings({ autoExitRatio: 0.5, autoExitLateMinute: "80", junk: 1 });
  assert.deepEqual(Object.keys(out).sort(), [...SETTING_FIELDS].sort());
  assert.equal(out.autoExitRatio, 0.5);
  assert.equal(out.autoExitLateMinute, 80);
  assert.equal(out.autoExitHardDeficit, null);
  assert.equal("junk" in out, false);
  assert.deepEqual(normalizeSettings(null), normalizeSettings(undefined));
});

test("resolveExitSettings: 키별 → 전역 → 기본값 순서로 필드마다 고른다", async () => {
  const db = new FakeFirestore({ "config/pro_settings": { autoExitRatio: 0.7, autoExitLateMinute: 80 } });
  const resolved = await resolveExitSettings(db, { autoExitHardDeficit: 4, autoExitLateMinute: "x" });
  assert.equal(resolved.autoExitRatio, 0.7, "키별 없음 → 전역");
  assert.equal(resolved.autoExitHardDeficit, 4, "키별 값");
  assert.equal(resolved.autoExitLateMinute, 80, "키별 깨짐 → 전역");
  assert.equal(resolved.autoExitBaseDeficit, FIELD_SPECS.autoExitBaseDeficit.def, "둘 다 없음 → 기본값");
  assert.equal(resolved.autoExitLateDeficit, 1);
  assert.equal(resolved.autoExitLateRatio, null, "후반 비율 기본은 null(기본 비율 따름)");
});

test("toClientPayload 는 클라이언트 이름(snake_case)으로 바꾸고 null 을 유지한다", async () => {
  const db = new FakeFirestore();
  const payload = toClientPayload(await resolveExitSettings(db, { autoExitLateRatio: 1 }));
  assert.deepEqual(payload, {
    ratio: 0.4, base_deficit: 2, hard_deficit: 3, late_minute: 70, late_deficit: 1, late_ratio: 1,
  });
  const defaults = toClientPayload(await resolveExitSettings(db, {}));
  assert.equal(defaults.late_ratio, null);
});

test("writeProSettings: 보낸 필드만 저장·null 은 지움·불량은 거부·모르는 필드는 거부", async () => {
  const db = new FakeFirestore({ "config/pro_settings": { autoExitRatio: 0.4, keep: "me" } });
  await writeProSettings(db, { autoExitHardDeficit: 4, autoExitLateRatio: null }, 123);
  const doc = db.read("config/pro_settings");
  assert.equal(doc.autoExitHardDeficit, 4);
  assert.equal(doc.autoExitLateRatio, null);
  assert.equal(doc.autoExitRatio, 0.4, "안 보낸 필드는 그대로");
  assert.equal(doc.keep, "me");
  assert.equal(doc.updatedAt, 123);
  await assert.rejects(() => writeProSettings(db, { autoExitHardDeficit: 99 }), /0~9/);
  await assert.rejects(() => writeProSettings(db, { bogus: 1 }), /모르는/);
  await assert.rejects(() => writeProSettings(db, {}), /저장할/);
  // 쓰기 뒤 같은 인스턴스는 즉시 새 값을 읽는다.
  assert.equal((await readProSettings(db)).autoExitHardDeficit, 4);
});

test("effectiveSettings 는 null 을 기본값으로 채운다(후반 비율은 null 그대로)", () => {
  const out = effectiveSettings(normalizeSettings({ autoExitRatio: 0.6 }));
  assert.equal(out.autoExitRatio, 0.6);
  assert.equal(out.autoExitBaseDeficit, 2);
  assert.equal(out.autoExitHardDeficit, 3);
  assert.equal(out.autoExitLateMinute, 70);
  assert.equal(out.autoExitLateDeficit, 1);
  assert.equal(out.autoExitLateRatio, null);
});


test("전역 문서 읽기가 실패해도 키별 값과 기본값으로 응답한다(리뷰 지적)", async () => {
  const broken = {
    collection() {
      return { doc() { return { async get() { throw new Error("quota exceeded"); } }; } };
    },
  };
  const resolved = await resolveExitSettings(broken, { autoExitHardDeficit: 4 });
  assert.equal(resolved.autoExitHardDeficit, 4, "키별 값이 사라졌다");
  assert.equal(resolved.autoExitRatio, DEFAULT_AUTO_EXIT_RATIO);
  assert.equal(resolved.autoExitLateMinute, 70);
});
