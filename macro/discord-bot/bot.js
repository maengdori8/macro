const {
  Client,
  GatewayIntentBits,
  PermissionFlagsBits,
  ChannelType,
} = require("discord.js");

// ─── 설정 ───
const DISCORD_TOKEN = process.env.DISCORD_TOKEN;
const API_BASE = process.env.API_BASE || "https://license-server-flame-eta.vercel.app/api";
const STATUS_API = process.env.STATUS_API || `${API_BASE}/status`;
const LINK_API = process.env.LINK_API || `${API_BASE}/discord-link`;
const BOT_API_KEY = process.env.BOT_API_KEY;
const PREFIX = "!";
const BUYER_ROLE = "구매자";

// ─── 봇 초기화 ───
const client = new Client({
  intents: [
    GatewayIntentBits.Guilds,
    GatewayIntentBits.GuildMessages,
    GatewayIntentBits.MessageContent,
    GatewayIntentBits.GuildMembers, // 역할 부여용 (개발자 포털에서 Server Members Intent 켜야 함)
  ],
});

// ─── HTTP ───
async function apiPost(url, body) {
  const res = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-Bot-Key": BOT_API_KEY },
    body: JSON.stringify(body),
    signal: AbortSignal.timeout(8000),
  });
  try {
    return await res.json();
  } catch {
    throw new Error(`서버 응답 파싱 실패 (HTTP ${res.status})`);
  }
}

async function fetchStatus(discordId) {
  const res = await fetch(`${STATUS_API}?discordId=${encodeURIComponent(discordId)}`, {
    headers: { "X-Bot-Key": BOT_API_KEY },
    signal: AbortSignal.timeout(8000),
  });
  try {
    return await res.json();
  } catch {
    throw new Error(`서버 응답 파싱 실패 (HTTP ${res.status})`);
  }
}

function timeAgo(ms) {
  if (!ms) return "알 수 없음";
  const diff = Date.now() - ms;
  if (diff < 60000) return "방금 전";
  if (diff < 3600000) return `${Math.floor(diff / 60000)}분 전`;
  if (diff < 86400000) return `${Math.floor(diff / 3600000)}시간 전`;
  return `${Math.floor(diff / 86400000)}일 전`;
}

async function safeReply(msg, text) {
  try {
    await msg.reply(text);
  } catch (e) {
    console.error("reply 실패:", e?.message || e);
  }
}

// 원본 메시지를 삭제한 뒤(=reply 불가)에는 채널에 멘션으로 결과를 보냅니다.
async function notify(msg, text) {
  try {
    await msg.channel.send({
      content: `<@${msg.author.id}> ${text}`,
      allowedMentions: { users: [msg.author.id] },
    });
  } catch (e) {
    console.error("notify 실패:", e?.message || e);
  }
}

async function resolveMe(guild) {
  let me = guild.members.me;
  if (!me) {
    try {
      me = await guild.members.fetchMe();
    } catch (e) {
      console.error("봇 멤버 조회 실패:", e?.message || e);
    }
  }
  return me;
}

// ─── 서버 구축 청사진 (채널별 보기/채팅 권한 명시) ───
// OPEN        : @everyone 보기+채팅
// READONLY    : @everyone 보기 O, 채팅 X (공지/규칙/FAQ)
// BUYER_WRITE : @everyone 보기 O, 채팅은 구매자만 (후기)
// BUYER_ONLY  : 구매자만 보기+채팅 (비구매자에겐 채널 자체가 안 보임)
// BUYER_READ  : 구매자만 보기, 채팅 X (다운로드/사용법 — 관리자만 게시)
const P = { OPEN: "OPEN", READONLY: "READONLY", BUYER_WRITE: "BUYER_WRITE", BUYER_ONLY: "BUYER_ONLY", BUYER_READ: "BUYER_READ" };

const BLUEPRINT = [
  {
    category: "📢 환영",
    catPerm: P.OPEN,
    channels: [
      { name: "공지", perm: P.READONLY },
      { name: "규칙", perm: P.READONLY },
      { name: "인증", perm: P.OPEN },
    ],
  },
  {
    category: "💎 구매-체험",
    catPerm: P.OPEN,
    channels: [
      { name: "무료체험-신청", perm: P.OPEN },
      { name: "구매방법", perm: P.READONLY },
      { name: "후기", perm: P.BUYER_WRITE },
    ],
  },
  {
    category: "🔒 구매자전용",
    catPerm: P.BUYER_ONLY,
    channels: [
      { name: "다운로드", perm: P.BUYER_READ },
      { name: "사용법", perm: P.BUYER_READ },
      { name: "내등수", perm: P.BUYER_ONLY },
      { name: "문의", perm: P.BUYER_ONLY },
    ],
  },
  {
    category: "🛠 지원",
    catPerm: P.OPEN,
    channels: [{ name: "자주묻는질문", perm: P.READONLY }],
  },
];

const V = PermissionFlagsBits.ViewChannel;
const S = PermissionFlagsBits.SendMessages;

// 권한 타입 → permissionOverwrites 배열. 봇은 항상 보기+채팅 허용(모든 동작 보장).
function permsFor(type, everyoneId, buyerId, botId) {
  const botAllow = { id: botId, allow: [V, S] };
  switch (type) {
    case P.READONLY:
      return [{ id: everyoneId, allow: [V], deny: [S] }, botAllow];
    case P.BUYER_WRITE:
      return [{ id: everyoneId, allow: [V], deny: [S] }, { id: buyerId, allow: [S] }, botAllow];
    case P.BUYER_ONLY:
      return [{ id: everyoneId, deny: [V] }, { id: buyerId, allow: [V, S] }, botAllow];
    case P.BUYER_READ:
      return [{ id: everyoneId, deny: [V] }, { id: buyerId, allow: [V], deny: [S] }, botAllow];
    case P.OPEN:
    default:
      return [{ id: everyoneId, allow: [V, S] }, botAllow];
  }
}

async function ensureRole(guild, name) {
  const existing = guild.roles.cache.find((r) => r.name === name);
  if (existing) return existing;
  return guild.roles.create({ name, mentionable: false, reason: "매크로 판매 서버 구축" });
}

async function buildServer(guild, botId) {
  const everyoneId = guild.roles.everyone.id;
  const buyerRole = await ensureRole(guild, BUYER_ROLE);
  let processed = 0;

  for (const group of BLUEPRINT) {
    const catPerms = permsFor(group.catPerm, everyoneId, buyerRole.id, botId);
    let cat = guild.channels.cache.find(
      (c) => c.type === ChannelType.GuildCategory && c.name === group.category
    );
    if (!cat) {
      cat = await guild.channels.create({
        name: group.category,
        type: ChannelType.GuildCategory,
        permissionOverwrites: catPerms,
      });
    } else {
      // 기존 카테고리도 권한 강제 동기화
      await cat.permissionOverwrites.set(catPerms);
    }
    processed++;

    for (const ch of group.channels) {
      const chPerms = permsFor(ch.perm, everyoneId, buyerRole.id, botId);
      const exists = guild.channels.cache.find(
        (c) => c.type === ChannelType.GuildText && c.name === ch.name && c.parentId === cat.id
      );
      if (!exists) {
        await guild.channels.create({
          name: ch.name,
          type: ChannelType.GuildText,
          parent: cat.id,
          permissionOverwrites: chPerms,
        });
      } else {
        // 기존 채널 권한도 강제 동기화(보기/채팅 싹 다 재설정)
        await exists.permissionOverwrites.set(chPerms);
      }
      processed++;
    }
  }
  return { created: processed, buyerRole };
}

// ─── 메시지 처리 ───
client.on("messageCreate", async (msg) => {
  if (msg.author.bot) return;
  if (!msg.content.startsWith(PREFIX)) return;

  const args = msg.content.slice(PREFIX.length).trim().split(/\s+/);
  const cmd = args[0].toLowerCase();

  // !인증 <키> — 라이센스 검증 후 구매자 역할 자동 부여
  if (cmd === "인증" || cmd === "auth") {
    if (!msg.guild) return safeReply(msg, "❌ 서버의 #인증 채널에서 사용하세요.");
    if (!msg.member) return safeReply(msg, "❌ 사용자 정보를 조회할 수 없습니다. 잠시 후 다시 시도하세요.");
    const key = (args[1] || "").trim();
    if (!key) return safeReply(msg, "❌ 사용법: `!인증 <라이센스키>`");

    // 키가 채팅에 남지 않도록 삭제. 삭제 권한이 없으면 처리하지 않고 거부(키 노출 방지).
    const me = await resolveMe(msg.guild);
    if (!me || !me.permissions.has(PermissionFlagsBits.ManageMessages)) {
      return safeReply(
        msg,
        "❌ 봇에 '메시지 관리' 권한이 없어 키가 노출될 수 있습니다. 방금 보낸 키를 직접 삭제하고, 관리자에게 권한 부여를 요청하세요."
      );
    }
    try {
      await msg.delete();
    } catch (e) {
      console.error("메시지 삭제 실패:", e?.message || e);
      return notify(msg, "❌ 보안을 위해 메시지를 삭제하지 못했습니다. 키를 직접 삭제하고 다시 시도하세요.");
    }

    // 이 시점부터 원본 메시지는 삭제됨 → reply 대신 notify(채널 멘션) 사용.
    try {
      const data = await apiPost(LINK_API, { key, discordId: msg.author.id });
      if (!data.success) {
        return notify(msg, `❌ ${data.message || "인증 실패"}`);
      }
      const role = msg.guild.roles.cache.find((r) => r.name === BUYER_ROLE);
      if (!role) {
        return notify(msg, "✅ 라이센스는 유효하지만 `구매자` 역할이 없습니다. 관리자가 `!서버구축`을 먼저 실행해야 합니다.");
      }
      try {
        await msg.member.roles.add(role);
      } catch (e) {
        console.error("역할 부여 실패:", e?.message || e);
        return notify(msg, "✅ 인증됐지만 역할 부여에 실패했습니다. (봇 권한/역할 순서 확인)");
      }
      const left = data.unlimited ? "무제한" : `${data.remainingDays}일 남음`;
      return notify(msg, `✅ 인증 완료! \`구매자\` 역할이 부여되었습니다. (${left})`);
    } catch (e) {
      console.error("인증 오류:", e?.message || e);
      const reason = e?.name === "TimeoutError" ? "서버 응답 시간 초과" : "서버 연결 실패";
      return notify(msg, `❌ ${reason}. 잠시 후 다시 시도하세요.`);
    }
  }

  // !서버구축 — 관리자 전용: 채널/역할/권한 자동 생성
  if (cmd === "서버구축" || cmd === "setup") {
    if (!msg.guild) return;
    if (!msg.member) return safeReply(msg, "❌ 사용자 정보를 조회할 수 없습니다.");
    if (!msg.member.permissions.has(PermissionFlagsBits.Administrator)) {
      return safeReply(msg, "❌ 관리자만 사용할 수 있습니다.");
    }
    const me = await resolveMe(msg.guild);
    if (!me) {
      return safeReply(msg, "❌ 봇 정보를 불러오지 못했습니다. 잠시 후 다시 시도하세요.");
    }
    if (!me.permissions.has(PermissionFlagsBits.ManageChannels) || !me.permissions.has(PermissionFlagsBits.ManageRoles)) {
      return safeReply(msg, "❌ 봇에 '채널 관리'와 '역할 관리' 권한이 필요합니다.");
    }
    await safeReply(msg, "🛠 서버 구축 + 권한 설정 중...");
    try {
      const { created } = await buildServer(msg.guild, me.id);
      return safeReply(msg, `✅ 서버 구축 + 권한 설정 완료! (${created}개 처리)\n채널별 보기/채팅 권한까지 모두 적용했습니다.\n이제 #인증 채널에서 \`!인증 <키>\`로 구매자 역할을 받을 수 있습니다.`);
    } catch (e) {
      console.error("서버구축 오류:", e?.message || e);
      return safeReply(msg, `❌ 서버 구축 실패: ${e?.message || e}\n(봇 역할이 충분히 높은지 확인하세요)`);
    }
  }

  // !등수 — 현재 등수 조회
  if (cmd === "등수" || cmd === "rank") {
    try {
      const data = await fetchStatus(msg.author.id);
      if (!data.success) return safeReply(msg, `❌ ${data.message}`);
      const status = data.running ? "🟢 실행 중" : "🔴 중지됨";
      const rank = data.rank !== null && data.rank !== undefined ? `**${data.rank}등**` : "측정 중...";
      return safeReply(
        msg,
        `📊 **매크로 상태**\n상태: ${status}\n등수: ${rank}\n` +
          `${data.message ? `메시지: ${data.message}\n` : ""}마지막 업데이트: ${timeAgo(data.updatedAt)}`
      );
    } catch (e) {
      console.error("fetchStatus 오류:", e?.message || e);
      const reason = e?.name === "TimeoutError" ? "서버 응답 시간 초과" : "서버 연결 실패";
      return safeReply(msg, `❌ ${reason}. 잠시 후 다시 시도하세요.`);
    }
  }

  // !도움
  if (cmd === "도움" || cmd === "help") {
    return safeReply(
      msg,
      `📋 **명령어 목록**\n` +
        `\`!인증 <키>\` - 라이센스 인증 후 구매자 역할 받기\n` +
        `\`!등수\` - 현재 매크로 등수 조회\n` +
        `\`!도움\` - 명령어 목록\n` +
        `\`!서버구축\` - (관리자) 채널/역할 자동 생성`
    );
  }
});

process.on("unhandledRejection", (e) => {
  console.error("unhandledRejection:", e?.message || e);
});

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
