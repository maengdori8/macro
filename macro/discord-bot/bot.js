const { Client, GatewayIntentBits } = require("discord.js");

// ─── 설정 ───
const DISCORD_TOKEN = process.env.DISCORD_TOKEN;
const STATUS_API = process.env.STATUS_API || "https://license-server-flame-eta.vercel.app/api/status";
const BOT_API_KEY = process.env.BOT_API_KEY; // 서버 status GET 인증용
const PREFIX = "!";

// ─── 봇 초기화 ───
const client = new Client({
  intents: [
    GatewayIntentBits.Guilds,
    GatewayIntentBits.GuildMessages,
    GatewayIntentBits.MessageContent,
  ],
});

// ─── API 호출 ───
async function fetchStatus(discordId) {
  const url = `${STATUS_API}?discordId=${encodeURIComponent(discordId)}`;
  // 타임아웃: 서버가 멈춰도 명령이 영원히 매달리지 않도록 8초 제한.
  const res = await fetch(url, {
    headers: { "X-Bot-Key": BOT_API_KEY },
    signal: AbortSignal.timeout(8000),
  });
  // 4xx/5xx 응답 본문이 JSON이 아닐 수 있으므로 방어적으로 파싱.
  let data;
  try {
    data = await res.json();
  } catch {
    throw new Error(`서버 응답 파싱 실패 (HTTP ${res.status})`);
  }
  return data;
}

// ─── 시간 포맷 ───
function timeAgo(ms) {
  if (!ms) return "알 수 없음";
  const diff = Date.now() - ms;
  if (diff < 60000) return "방금 전";
  if (diff < 3600000) return `${Math.floor(diff / 60000)}분 전`;
  if (diff < 86400000) return `${Math.floor(diff / 3600000)}시간 전`;
  return `${Math.floor(diff / 86400000)}일 전`;
}

// reply가 실패(권한 부족, 길이 초과 등)해도 핸들러가 죽지 않도록 감쌉니다.
async function safeReply(msg, text) {
  try {
    await msg.reply(text);
  } catch (e) {
    console.error("reply 실패:", e?.message || e);
  }
}

// ─── 메시지 처리 ───
client.on("messageCreate", async (msg) => {
  if (msg.author.bot) return;
  if (!msg.content.startsWith(PREFIX)) return;

  const args = msg.content.slice(PREFIX.length).trim().split(/\s+/);
  const cmd = args[0].toLowerCase();

  // !등수 - 현재 등수 조회
  if (cmd === "등수" || cmd === "rank") {
    try {
      const data = await fetchStatus(msg.author.id);
      if (!data.success) {
        return safeReply(msg, `❌ ${data.message}`);
      }

      const status = data.running ? "🟢 실행 중" : "🔴 중지됨";
      const rank = data.rank !== null && data.rank !== undefined ? `**${data.rank}등**` : "측정 중...";
      const updated = timeAgo(data.updatedAt);

      return safeReply(
        msg,
        `📊 **매크로 상태**\n` +
          `상태: ${status}\n` +
          `등수: ${rank}\n` +
          `${data.message ? `메시지: ${data.message}\n` : ""}` +
          `마지막 업데이트: ${updated}`
      );
    } catch (e) {
      console.error("fetchStatus 오류:", e?.message || e);
      const reason = e?.name === "TimeoutError" ? "서버 응답 시간 초과" : "서버 연결 실패";
      return safeReply(msg, `❌ ${reason}. 잠시 후 다시 시도하세요.`);
    }
  }

  // !도움 - 명령어 목록
  if (cmd === "도움" || cmd === "help") {
    return safeReply(
      msg,
      `📋 **명령어 목록**\n` +
        `\`!등수\` - 현재 매크로 등수 조회\n` +
        `\`!도움\` - 명령어 목록`
    );
  }
});

// 처리되지 않은 거부로 프로세스가 죽지 않도록 안전망.
process.on("unhandledRejection", (e) => {
  console.error("unhandledRejection:", e?.message || e);
});

// ─── 봇 시작 ───
client.once("ready", () => {
  console.log(`✅ 봇 로그인: ${client.user.tag}`);
});

if (!DISCORD_TOKEN) {
  console.error("❌ DISCORD_TOKEN 환경변수가 설정되지 않았습니다.");
  process.exit(1);
}
if (!BOT_API_KEY) {
  console.error("❌ BOT_API_KEY 환경변수가 설정되지 않았습니다.");
  process.exit(1);
}

client.login(DISCORD_TOKEN);
