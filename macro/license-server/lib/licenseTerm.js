"use strict";

// 라이센스 유효기간 계산을 한 곳으로 모은 헬퍼.
//
// 왜: 만료 계산(createdAt + days*86400000)이 verify/admin/discord-link/expiring/my-license
// 다섯 곳에 복사돼 있었다. 시간 단위(1~24시간)를 추가하면서 한 곳이라도 빠뜨리면
// 그 경로만 만료를 잘못 판정한다(= 만료된 키가 통과하거나 정상 키가 거부됨).
// 그래서 계산을 여기로 모으고 각 엔드포인트는 이 함수만 부른다.
//
// 데이터 모델(하위 호환):
//   - 기존 키: { days: N }                 → N일
//   - 무제한  : { days: 99999 }            → 만료 없음
//   - 신규    : { days: 0, hours: N }      → N시간
//   hours가 0보다 크면 hours가 우선한다. 기존 문서에는 hours 필드가 없으므로
//   undefined → 0으로 떨어져 기존 동작이 그대로 유지된다.

const UNLIMITED_DAYS = 99999;
const MS_PER_HOUR = 3600000;
const MS_PER_DAY = 86400000;
const MAX_ISSUE_HOURS = 24;

function toMillis(createdAt) {
  return createdAt?.toMillis?.() ?? createdAt ?? 0;
}

function isUnlimited(data) {
  return Number(data?.days || 0) === UNLIMITED_DAYS;
}

// 유효기간 길이(ms). 무제한(99999일)도 Infinity가 아니라 유한값으로 둔다.
// Infinity를 쓰면 서명 페이로드의 exp가 JSON에서 null이 되어 무제한 키가 전부 깨진다.
// 99999일 ≈ 273년이라 실질적으로 무제한이고, 기존 계산식과 값이 완전히 동일하다.
function durationMs(data) {
  if (isUnlimited(data)) return UNLIMITED_DAYS * MS_PER_DAY;
  const hours = Number(data?.hours || 0);
  if (hours > 0) return hours * MS_PER_HOUR;
  return Number(data?.days || 0) * MS_PER_DAY;
}

// 만료 시각(ms). 항상 유한값이다.
function expiresAt(data) {
  return toMillis(data?.createdAt) + durationMs(data);
}

function isExpired(data, now = Date.now()) {
  return now > expiresAt(data);
}

// 남은 시간을 사람이 읽는 문구로. 시간권은 '일'로 반올림하면 1시간권이 1일로 보이므로
// 시간·분 단위로 표기한다.
function remainingText(data, now = Date.now()) {
  if (isUnlimited(data)) return "무제한";
  const left = expiresAt(data) - now;
  if (left <= 0) return "만료됨";
  if (left >= MS_PER_DAY) return `${Math.ceil(left / MS_PER_DAY)}일 남음`;
  if (left >= MS_PER_HOUR) return `${Math.floor(left / MS_PER_HOUR)}시간 남음`;
  return `${Math.max(1, Math.ceil(left / 60000))}분 남음`;
}

// 상품명 표기(예: "3시간권", "7일권", "무제한").
function termText(data) {
  if (isUnlimited(data)) return "무제한";
  const hours = Number(data?.hours || 0);
  if (hours > 0) return `${hours}시간권`;
  return `${Number(data?.days || 0)}일권`;
}

module.exports = {
  UNLIMITED_DAYS,
  MAX_ISSUE_HOURS,
  MS_PER_HOUR,
  MS_PER_DAY,
  isUnlimited,
  durationMs,
  expiresAt,
  isExpired,
  remainingText,
  termText,
};
