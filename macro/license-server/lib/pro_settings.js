"use strict";

// 프로(macro_pro) 공통 운영 설정 — 0:2 자동 종료 비율.
//
// 왜 서버에 있나: 비율(기본 40%)은 클라이언트 상수였는데, 비매너 점수 실측에 따라
// 값을 조정할 일이 생긴다. 재빌드 없이 관리 화면에서 바꾸면 모든 프로 클라이언트가
// 다음 인증(앱 시작) 때 새 값을 받아 간다.
//
// ⚠️ 이 값은 서명에 **안 묶인다**(운영값). v2 서명 메시지에 필드를 추가하면 이미
// 배포된 클라이언트가 전부 검증에 실패한다(메시지 규칙이 클라이언트 상수와 정확히
// 같아야 하므로). 위조로 얻는 것도 '구매자 자신의 종료 비율 조절'뿐이라 신뢰 판정과
// 무관하고, 클라이언트는 범위 밖 값을 버리고 내장 기본값(40%)으로 동작한다(fail-safe).

const SETTINGS_COLLECTION = "config";
const SETTINGS_DOC_ID = "pro_settings";
const DEFAULT_AUTO_EXIT_RATIO = 0.4;

// 비율을 받는 제품. 일반(macro)에는 종료 기능 자체가 없고, mPause 는 수동 단품이다.
const AUTO_EXIT_PRODUCT = "macro_pro";

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

function autoExitRatioAppliesTo(product) {
  return product === AUTO_EXIT_PRODUCT;
}

async function readAutoExitRatio(db, now = Date.now()) {
  const cached = cacheByDb.get(db);
  if (cached && now < cached.expiresAt) return cached.value;
  const doc = await db.collection(SETTINGS_COLLECTION).doc(SETTINGS_DOC_ID).get();
  const stored = doc.exists ? normalizeAutoExitRatio(doc.data().autoExitRatio) : null;
  const value = stored === null ? DEFAULT_AUTO_EXIT_RATIO : stored;
  cacheByDb.set(db, { value, expiresAt: now + CACHE_TTL_MS });
  return value;
}

// 라이센스 문서에 키별 비율을 두는 필드. 없거나(null) 깨졌으면 전역값을 쓴다.
const LICENSE_RATIO_FIELD = "autoExitRatio";

// 구매자 한 명에게 줄 최종 비율: **키별 값이 있으면 그것, 없으면 전역값.**
// 키별 값은 라이센스 문서 안에 있어서 verify 가 이미 읽은 문서에서 그냥 꺼낸다 —
// 추가 Firestore 읽기 0. 전역값은 기존 TTL 캐시 경로 그대로.
async function resolveAutoExitRatio(db, licenseData, now = Date.now()) {
  const own = normalizeAutoExitRatio(
    licenseData ? licenseData[LICENSE_RATIO_FIELD] : undefined
  );
  if (own !== null) return own;
  return readAutoExitRatio(db, now);
}

async function writeAutoExitRatio(db, value, updatedAt) {
  const ratio = normalizeAutoExitRatio(value);
  if (ratio === null) {
    // 관리 API 가 먼저 검증하지만, 여기가 저장 직전의 마지막 관문이다.
    throw new Error("자동 종료 비율은 0~1 사이 숫자여야 합니다.");
  }
  await db
    .collection(SETTINGS_COLLECTION)
    .doc(SETTINGS_DOC_ID)
    .set(
      {
        autoExitRatio: ratio,
        ...(updatedAt === undefined ? {} : { updatedAt }),
      },
      { merge: true }
    );
  // 같은 인스턴스는 즉시 새 값을 읽게 한다(다른 인스턴스는 TTL 로 따라온다).
  cacheByDb.delete(db);
  return ratio;
}

module.exports = {
  DEFAULT_AUTO_EXIT_RATIO,
  LICENSE_RATIO_FIELD,
  normalizeAutoExitRatio,
  autoExitRatioAppliesTo,
  readAutoExitRatio,
  resolveAutoExitRatio,
  writeAutoExitRatio,
};
