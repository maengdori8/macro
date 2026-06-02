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
  const res = await fetch(url, {
    headers: { "X-Bot-Key": BOT_API_KEY || "" },
  });
  return res.json();
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
        return msg.reply(`❌ ${data.message}`);
      }

      const status = data.running ? "🟢 실행 중" : "🔴 중지됨";
      const rank = data.rank !== null ? `**${data.rank}등**` : "측정 중...";
      const updated = timeAgo(data.updatedAt);

      return msg.reply(
        `📊 **매크로 상태**\n` +
          `상태: ${status}\n` +
          `등수: ${rank}\n` +
          `${data.message ? `메시지: ${data.message}\n` : ""}` +
          `마지막 업데이트: ${updated}`
      );
    } catch (e) {
      return msg.reply("❌ 서버 연결에 실패했습니다.");
    }
  }

  // !도움 - 명령어 목록
  if (cmd === "도움" || cmd === "help") {
    return msg.reply(
      `📋 **명령어 목록**\n` +
        `\`!등수\` - 현재 매크로 등수 조회\n` +
        `\`!도움\` - 명령어 목록`
    );
  }
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
