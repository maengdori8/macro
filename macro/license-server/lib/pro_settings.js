"use strict";

// 프로(macro_pro) 운영 설정 — 자동 종료 규칙(비율·점차·후반 분).
//
// 왜 서버에 있나: 처음엔 비율(40%) 하나가 클라이언트 상수였는데, 비매너 점수 실측에
// 따라 값을 조정할 일이 생겼다. 재빌드 없이 관리 화면에서 바꾸면 모든 프로 클라이언트가
// 다음 인증(앱 시작) 때 새 값을 받아 간다. 이제는 규칙이 셋이다:
//   기본   : 상대−나 ≥ baseDeficit(2)  → 비율(autoExitRatio)만큼만 나간다
//   대량   : 상대−나 ≥ hardDeficit(3)  → 비율 무시, 무조건 나간다(0=끔)
//   후반   : 경기 시계 ≥ lateMinute(70)분 이고 상대−나 ≥ lateDeficit(1)
//            → 비율(lateRatio, 없으면 autoExitRatio)만큼 나간다(0분=끔)
// 값은 **키별(라이센스 문서) → 전역(config/pro_settings) → 내장 기본값** 순으로 정해진다.
//
// ⚠️ 이 값들은 서명에 **안 묶인다**(운영값). v2 서명 메시지에 필드를 추가하면 이미
// 배포된 클라이언트가 전부 검증에 실패한다(메시지 규칙이 클라이언트 상수와 정확히
// 같아야 하므로). 위조로 얻는 것도 '구매자 자신의 종료 규칙 조절'뿐이라 신뢰 판정과
// 무관하고, 클라이언트는 범위 밖 값을 필드 단위로 버리고 기본값으로 동작한다(fail-safe).

const SETTINGS_COLLECTION = "config";
const SETTINGS_DOC_ID = "pro_settings";
const DEFAULT_AUTO_EXIT_RATIO = 0.4;

// 비율을 받는 제품. 일반(macro)에는 종료 기능 자체가 없고, mPause 는 수동 단품이다.
const AUTO_EXIT_PRODUCT = "macro_pro";

// 필드 규격 — 서버 문서 이름(camelCase) ↔ 클라이언트 JSON 이름(snake_case) ↔ 범위·기본값.
// 범위는 클라이언트 auto_exit.SETTING_BOUNDS 와 같아야 한다(다르면 서버가 받은 값을
// 클라이언트가 버려 "패널에서 바꿨는데 안 바뀐다"가 된다).
const FIELD_SPECS = {
  autoExitRatio: { kind: "ratio", def: DEFAULT_AUTO_EXIT_RATIO, client: "ratio" },
  autoExitBaseDeficit: { kind: "int", min: 1, max: 9, def: 2, client: "base_deficit" },
  autoExitHardDeficit: { kind: "int", min: 0, max: 9, def: 3, client: "hard_deficit" },
  autoExitLateMinute: { kind: "int", min: 0, max: 120, def: 70, client: "late_minute" },
  autoExitLateDeficit: { kind: "int", min: 1, max: 9, def: 1, client: "late_deficit" },
  // null 이 정상값이다 — '기본 비율을 따른다'.
  autoExitLateRatio: { kind: "ratio", def: null, client: "late_ratio" },
};
const SETTING_FIELDS = Object.keys(FIELD_SPECS);
// 라이센스 문서에 키별 비율을 두는 필드(호환용 이름).
const LICENSE_RATIO_FIELD = "autoExitRatio";

// 인스턴스별 읽기 캐시. verify 마다 문서를 읽으면 Firestore 읽기 할당량을 잠식한다
// (할당량 소진으로 인증이 통째로 멈춘 실제 장애 이력이 있다). TTL 안에서는 재읽기
// 하지 않으므로, 값 변경은 인스턴스별로 최대 1분 늦게 퍼진다 — 운영값이라 충분하다.
const CACHE_TTL_MS = 60 * 1000;
const cacheByDb = new WeakMap();

// 0~1 사이 유한한 숫자만 통과시킨다. 그 외(음수·1 초과·NaN·불리언·빈 문자열)는
// 전부 null — 저장도 전송도 하지 않는다.
function normalizeAutoExitRatio(value) {
  if (typeof value !== "number" && typeof value !== "string") return null;
  if (typeof value === "string" && value.trim() === "") return null;
  const ratio = Number(value);
  if (!Number.isFinite(ratio) || ratio < 0 || ratio > 1) return null;
  return ratio;
}

function normalizeInt(value, min, max) {
  if (typeof value !== "number" && typeof value !== "string") return null;
  if (typeof value === "string" && value.trim() === "") return null;
  const number = Number(value);
  if (!Number.isInteger(number) || number < min || number > max) return null;
  return number;
}

// 필드 하나를 규격대로 검증한다. 불량·없음은 null.
function normalizeSetting(name, value) {
  const spec = FIELD_SPECS[name];
  if (!spec) return null;
  return spec.kind === "ratio"
    ? normalizeAutoExitRatio(value)
    : normalizeInt(value, spec.min, spec.max);
}

// 문서(또는 임의 객체)에서 설정 필드 전부를 검증해 꺼낸다. 없거나 깨진 필드는 null.
function normalizeSettings(source) {
  const out = {};
  for (const name of SETTING_FIELDS) {
    out[name] = normalizeSetting(name, source ? source[name] : undefined);
  }
  return out;
}

function isSettingField(name) {
  return Object.prototype.hasOwnProperty.call(FIELD_SPECS, name);
}

function autoExitRatioAppliesTo(product) {
  return product === AUTO_EXIT_PRODUCT;
}

// 전역 설정(검증된 값, 없으면 null)을 캐시와 함께 읽는다.
async function readProSettings(db, now = Date.now()) {
  const cached = cacheByDb.get(db);
  if (cached && now < cached.expiresAt) return { ...cached.value };
  const doc = await db.collection(SETTINGS_COLLECTION).doc(SETTINGS_DOC_ID).get();
  const value = normalizeSettings(doc.exists ? doc.data() : null);
  cacheByDb.set(db, { value, expiresAt: now + CACHE_TTL_MS });
  return { ...value };
}

// 전역 설정의 **실효값**(값 ?? 기본값) — 관리 화면 표시용.
function effectiveSettings(normalized) {
  const out = {};
  for (const name of SETTING_FIELDS) {
    const value = normalized ? normalized[name] : null;
    out[name] = value === null || value === undefined ? FIELD_SPECS[name].def : value;
  }
  return out;
}

async function readAutoExitRatio(db, now = Date.now()) {
  const settings = await readProSettings(db, now);
  return settings.autoExitRatio === null ? DEFAULT_AUTO_EXIT_RATIO : settings.autoExitRatio;
}

// 구매자 한 명에게 줄 최종 설정: **키별 값이 있으면 그것, 없으면 전역값, 그것도
// 없으면 기본값.** 키별 값은 라이센스 문서 안에 있어서 verify 가 이미 읽은 문서에서
// 그냥 꺼낸다 — 추가 Firestore 읽기 0. 전역값은 TTL 캐시 경로 그대로.
async function resolveExitSettings(db, licenseData, now = Date.now()) {
  const own = normalizeSettings(licenseData);
  const global = await readProSettings(db, now);
  const out = {};
  for (const name of SETTING_FIELDS) {
    if (own[name] !== null) out[name] = own[name];
    else if (global[name] !== null) out[name] = global[name];
    else out[name] = FIELD_SPECS[name].def;
  }
  return out;
}

async function resolveAutoExitRatio(db, licenseData, now = Date.now()) {
  return (await resolveExitSettings(db, licenseData, now)).autoExitRatio;
}

// 클라이언트 JSON(auto_exit_settings) 으로 바꾼다. null(후반 비율 없음)은 그대로 보낸다.
function toClientPayload(resolved) {
  const out = {};
  for (const name of SETTING_FIELDS) {
    out[FIELD_SPECS[name].client] = resolved[name];
  }
  return out;
}

// 라이센스 문서에서 설정 필드만 골라낸다(verify 가 license_service 결과로 넘기는 용도).
function pickSettingFields(data) {
  const out = {};
  for (const name of SETTING_FIELDS) {
    if (data && data[name] !== undefined) out[name] = data[name];
  }
  return out;
}

// 전역 설정 저장. patch 의 각 필드: null = 지움(기본값으로), 값 = 검증 후 저장.
// 모르는 필드는 무시하지 않고 거부한다 — 오타로 엉뚱한 키에 저장되는 조용한 사고 방지.
async function writeProSettings(db, patch, updatedAt) {
  const update = {};
  for (const [name, value] of Object.entries(patch || {})) {
    if (!isSettingField(name)) {
      throw new Error(`모르는 설정 필드입니다: ${name}`);
    }
    if (value === null) {
      update[name] = null;
      continue;
    }
    const normalized = normalizeSetting(name, value);
    if (normalized === null) {
      throw new Error(settingErrorMessage(name));
    }
    update[name] = normalized;
  }
  if (Object.keys(update).length === 0) {
    throw new Error("저장할 설정이 없습니다.");
  }
  await db
    .collection(SETTINGS_COLLECTION)
    .doc(SETTINGS_DOC_ID)
    .set(
      { ...update, ...(updatedAt === undefined ? {} : { updatedAt }) },
      { merge: true }
    );
  // 같은 인스턴스는 즉시 새 값을 읽게 한다(다른 인스턴스는 TTL 로 따라온다).
  cacheByDb.delete(db);
  return update;
}

async function writeAutoExitRatio(db, value, updatedAt) {
  const ratio = normalizeAutoExitRatio(value);
  if (ratio === null) {
    // 관리 API 가 먼저 검증하지만, 여기가 저장 직전의 마지막 관문이다.
    throw new Error("자동 종료 비율은 0~1 사이 숫자여야 합니다.");
  }
  await writeProSettings(db, { autoExitRatio: ratio }, updatedAt);
  return ratio;
}

function settingErrorMessage(name) {
  const spec = FIELD_SPECS[name];
  if (!spec) return `모르는 설정 필드입니다: ${name}`;
  return spec.kind === "ratio"
    ? "자동 종료 비율은 0~1 사이 숫자여야 합니다."
    : `${name} 은(는) ${spec.min}~${spec.max} 사이 정수여야 합니다.`;
}

module.exports = {
  DEFAULT_AUTO_EXIT_RATIO,
  FIELD_SPECS,
  SETTING_FIELDS,
  LICENSE_RATIO_FIELD,
  normalizeAutoExitRatio,
  normalizeSetting,
  normalizeSettings,
  isSettingField,
  effectiveSettings,
  autoExitRatioAppliesTo,
  readProSettings,
  readAutoExitRatio,
  resolveExitSettings,
  resolveAutoExitRatio,
  toClientPayload,
  pickSettingFields,
  writeProSettings,
  writeAutoExitRatio,
  settingErrorMessage,
};
