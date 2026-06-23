const {
  Client,
  GatewayIntentBits,
  PermissionFlagsBits,
  ChannelType,
  SlashCommandBuilder,
  EmbedBuilder,
  ActionRowBuilder,
  ButtonBuilder,
  ButtonStyle,
  ModalBuilder,
  TextInputBuilder,
  TextInputStyle,
} = require("discord.js");

// ─── 설정 ───
const DISCORD_TOKEN = process.env.DISCORD_TOKEN;
const API_BASE = process.env.API_BASE || "https://license-server-flame-eta.vercel.app/api";
const STATUS_API = process.env.STATUS_API || `${API_BASE}/status`;
const LINK_API = process.env.LINK_API || `${API_BASE}/discord-link`;
const MYLICENSE_API = process.env.MYLICENSE_API || `${API_BASE}/my-license`;
const EXPIRING_API = process.env.EXPIRING_API || `${API_BASE}/expiring`;
const BOT_API_KEY = process.env.BOT_API_KEY;
const PREFIX = "!";
const BUYER_ROLE = "구매자";
const EXPIRY_ALERT_DAYS = 3; // 만료 D-N 이내면 DM 알림

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

async function apiGet(url) {
  const res = await fetch(url, {
    headers: { "X-Bot-Key": BOT_API_KEY },
    signal: AbortSignal.timeout(8000),
  });
  try {
    return await res.json();
  } catch {
    throw new Error(`서버 응답 파싱 실패 (HTTP ${res.status})`);
  }
}

async function fetchStatus(discordId) {
  return apiGet(`${STATUS_API}?discordId=${encodeURIComponent(discordId)}`);
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

// ─── 인증/등수 공용 로직 (프리픽스·슬래시 공용, 결과 문자열 반환) ───
async function doAuth(guild, member, userId, key) {
  if (!guild || !member) return "❌ 서버 안에서 사용하세요.";
  if (!key) return "❌ 라이센스 키를 입력하세요.";
  try {
    const data = await apiPost(LINK_API, { key, discordId: userId });
    if (!data.success) return `❌ ${data.message || "인증 실패"}`;
    const role = guild.roles.cache.find((r) => r.name === BUYER_ROLE);
    if (!role) return "✅ 라이센스는 유효하지만 `구매자` 역할이 없습니다. 관리자가 `!서버구축`을 먼저 실행해야 합니다.";
    const hadRole = member.roles.cache.has(role.id);
    try {
      await member.roles.add(role);
    } catch (e) {
      console.error("역할 부여 실패:", e?.message || e);
      return "✅ 인증됐지만 역할 부여에 실패했습니다. (봇 권한/역할 순서 확인)";
    }
    const left = data.unlimited ? "무제한" : `${data.remainingDays}일 남음`;
    // 중복 등록: 이미 본인 키로 등록 + 이미 역할 보유 → 안내만
    if (data.alreadyLinked && hadRole) {
      return `ℹ️ 이미 인증되어 있습니다. (${left})`;
    }
    if (data.alreadyLinked) {
      return `✅ 이미 등록된 키입니다. \`구매자\` 역할을 확인했습니다. (${left})`;
    }
    return `✅ 인증 완료! \`구매자\` 역할이 부여되었습니다. (${left})`;
  } catch (e) {
    console.error("인증 오류:", e?.message || e);
    return e?.name === "TimeoutError"
      ? "❌ 서버 응답 시간 초과. 잠시 후 다시 시도하세요."
      : "❌ 서버 연결 실패. 잠시 후 다시 시도하세요.";
  }
}

async function rankText(userId) {
  try {
    const data = await fetchStatus(userId);
    if (!data.success) return `❌ ${data.message}`;
    const status = data.running ? "🟢 실행 중" : "🔴 중지됨";
    const rank = data.rank !== null && data.rank !== undefined ? `**${data.rank}등**` : "측정 중...";
    return (
      `📊 **매크로 상태**\n상태: ${status}\n등수: ${rank}\n` +
      `${data.message ? `메시지: ${data.message}\n` : ""}마지막 업데이트: ${timeAgo(data.updatedAt)}`
    );
  } catch (e) {
    console.error("fetchStatus 오류:", e?.message || e);
    return e?.name === "TimeoutError"
      ? "❌ 서버 응답 시간 초과. 잠시 후 다시 시도하세요."
      : "❌ 서버 연결 실패. 잠시 후 다시 시도하세요.";
  }
}

function fmtDate(ms) {
  if (!ms) return "-";
  const d = new Date(ms);
  const kst = new Date(d.getTime() + 9 * 3600000); // KST
  return `${kst.getUTCFullYear()}-${String(kst.getUTCMonth() + 1).padStart(2, "0")}-${String(kst.getUTCDate()).padStart(2, "0")}`;
}

async function licenseInfoText(userId) {
  try {
    const d = await apiGet(`${MYLICENSE_API}?discordId=${encodeURIComponent(userId)}`);
    if (!d.success) return `❌ ${d.message || "조회 실패"}`;
    if (!d.registered) return "❌ 등록된 라이센스가 없습니다. 먼저 `[🔑 인증]` 으로 인증하세요.";
    if (d.disabled) return "⛔ 비활성화된 라이센스입니다. 관리자에게 문의하세요.";
    if (d.expired) return "⏰ 만료된 라이센스입니다. `#구매방법` 에서 연장하세요.";
    const period = d.unlimited
      ? "♾️ 무제한"
      : `**${d.remainingDays}일 남음** (만료: ${fmtDate(d.expiresAt)})`;
    return (
      `📋 **내 라이센스**\n` +
      `상태: 🟢 정상\n` +
      `기간: ${period}\n` +
      `등록 기기: ${d.hwidCount}/${d.maxHwids}대`
    );
  } catch (e) {
    console.error("licenseInfo 오류:", e?.message || e);
    return e?.name === "TimeoutError"
      ? "❌ 서버 응답 시간 초과. 잠시 후 다시 시도하세요."
      : "❌ 서버 연결 실패. 잠시 후 다시 시도하세요.";
  }
}

const HELP_TEXT =
  `📋 **사용 방법**\n\n` +
  `🔑 **인증** — \`[🔑 인증]\` 버튼 또는 \`/인증\`\n` +
  `　라이센스 키를 입력하면 **구매자 역할**이 자동으로 부여됩니다.\n\n` +
  `📋 **내 라이센스** — \`[📋 내 라이센스]\` 버튼 또는 \`/내정보\`\n` +
  `　만료일 · 남은 기간 · 등록 기기 수를 확인합니다.\n\n` +
  `📊 **내 등수** — \`[📊 내 등수]\` 버튼 또는 \`/등수\`\n` +
  `　매크로 실시간 등수/상태를 확인합니다.\n\n` +
  `⏰ 만료 3일 전 DM으로 자동 알려드립니다.\n` +
  `❓ 문제가 있으면 **#문의** 채널로 알려주세요.`;

// ─── 패널(임베드 + 버튼) ───
const PANEL_IMAGE = process.env.PANEL_IMAGE || ""; // 배너 이미지 URL(선택)

function buildPanel() {
  const embed = new EmbedBuilder()
    .setColor(0x7ab7ff)
    .setTitle("🎮 FC ONLINE 감독모드 자동매크로")
    .setDescription("아래 버튼으로 **인증 / 등수 확인**을 진행하세요.\n키는 버튼 클릭 시 뜨는 비공개 입력창에 넣으면 됩니다 (채팅 노출 X).")
    .addFields(
      { name: "🔑 인증", value: "라이센스 키 → 구매자 역할 자동", inline: true },
      { name: "📊 내 등수", value: "내 매크로 실시간 현황", inline: true },
      { name: "🔄 업데이트", value: "패치돼도 자동 적용", inline: true }
    )
    .setFooter({ text: "문제가 있나요? #문의 채널로 알려주세요." });
  if (PANEL_IMAGE) embed.setImage(PANEL_IMAGE);

  const row = new ActionRowBuilder().addComponents(
    new ButtonBuilder().setCustomId("panel_auth").setLabel("인증").setEmoji("🔑").setStyle(ButtonStyle.Success),
    new ButtonBuilder().setCustomId("panel_license").setLabel("내 라이센스").setEmoji("📋").setStyle(ButtonStyle.Primary),
    new ButtonBuilder().setCustomId("panel_rank").setLabel("내 등수").setEmoji("📊").setStyle(ButtonStyle.Primary),
    new ButtonBuilder().setCustomId("panel_help").setLabel("도움말").setEmoji("❓").setStyle(ButtonStyle.Secondary)
  );
  return { embeds: [embed], components: [row] };
}

function buildAuthModal() {
  const input = new TextInputBuilder()
    .setCustomId("key")
    .setLabel("라이센스 키")
    .setStyle(TextInputStyle.Short)
    .setPlaceholder("XXXXX-XXXXX-XXXXX-XXXXX-XXXXX")
    .setRequired(true)
    .setMaxLength(64);
  return new ModalBuilder()
    .setCustomId("auth_modal")
    .setTitle("라이센스 인증")
    .addComponents(new ActionRowBuilder().addComponents(input));
}

// ─── 슬래시 명령 정의/등록 ───
const SLASH_COMMANDS = [
  new SlashCommandBuilder()
    .setName("인증")
    .setDescription("라이센스 키로 구매자 역할 받기 (본인만 보임)")
    .addStringOption((o) => o.setName("키").setDescription("라이센스 키").setRequired(true)),
  new SlashCommandBuilder().setName("등수").setDescription("현재 매크로 등수 조회 (본인만 보임)"),
  new SlashCommandBuilder().setName("내정보").setDescription("내 라이센스 만료일/남은기간 확인 (본인만 보임)"),
  new SlashCommandBuilder().setName("도움").setDescription("명령어 목록"),
].map((c) => c.toJSON());

async function registerGuildCommands(guild) {
  try {
    await guild.commands.set(SLASH_COMMANDS);
    console.log(`슬래시 명령 등록 완료: ${guild.name}`);
  } catch (e) {
    console.error(`슬래시 등록 실패(${guild?.id}). 봇을 applications.commands 스코프로 재초대했는지 확인:`, e?.message || e);
  }
}

// ─── 인터랙션 처리 (슬래시 / 버튼 / 모달) ───
client.on("interactionCreate", async (interaction) => {
  try {
    // 패널 버튼
    if (interaction.isButton()) {
      if (interaction.customId === "panel_auth") {
        return interaction.showModal(buildAuthModal());
      }
      if (interaction.customId === "panel_license") {
        await interaction.deferReply({ ephemeral: true });
        return interaction.editReply(await licenseInfoText(interaction.user.id));
      }
      if (interaction.customId === "panel_rank") {
        await interaction.deferReply({ ephemeral: true });
        return interaction.editReply(await rankText(interaction.user.id));
      }
      if (interaction.customId === "panel_help") {
        return interaction.reply({ content: HELP_TEXT, ephemeral: true });
      }
      return;
    }

    // 인증 모달 제출
    if (interaction.isModalSubmit()) {
      if (interaction.customId === "auth_modal") {
        const key = (interaction.fields.getTextInputValue("key") || "").trim();
        await interaction.deferReply({ ephemeral: true });
        return interaction.editReply(await doAuth(interaction.guild, interaction.member, interaction.user.id, key));
      }
      return;
    }

    // 슬래시 명령
    if (!interaction.isChatInputCommand()) return;
    const name = interaction.commandName;
    if (name === "인증") {
      const key = (interaction.options.getString("키") || "").trim();
      await interaction.deferReply({ ephemeral: true });
      return interaction.editReply(await doAuth(interaction.guild, interaction.member, interaction.user.id, key));
    }
    if (name === "등수") {
      await interaction.deferReply({ ephemeral: true });
      return interaction.editReply(await rankText(interaction.user.id));
    }
    if (name === "내정보") {
      await interaction.deferReply({ ephemeral: true });
      return interaction.editReply(await licenseInfoText(interaction.user.id));
    }
    if (name === "도움") {
      return interaction.reply({ content: HELP_TEXT, ephemeral: true });
    }
  } catch (e) {
    console.error("interaction 오류:", e?.message || e);
    try {
      if (interaction.deferred || interaction.replied) await interaction.editReply("❌ 처리 중 오류가 발생했습니다.");
      else if (interaction.isRepliable()) await interaction.reply({ content: "❌ 처리 중 오류가 발생했습니다.", ephemeral: true });
    } catch {}
  }
});

// ─── 메시지 처리 ───
client.on("messageCreate", async (msg) => {
  if (msg.author.bot) return;
  if (!msg.content.startsWith(PREFIX)) return;

  const args = msg.content.slice(PREFIX.length).trim().split(/\s+/);
  const cmd = args[0].toLowerCase();

  // !인증 (구버전) → 슬래시 명령으로 유도. 키가 노출됐으면 즉시 삭제.
  if (cmd === "인증" || cmd === "auth") {
    if (msg.guild) {
      const me = await resolveMe(msg.guild);
      if (me && me.permissions.has(PermissionFlagsBits.ManageMessages)) {
        await msg.delete().catch(() => {});
      }
    }
    return notify(
      msg,
      "🔒 보안을 위해 **`/인증`** 슬래시 명령어를 사용하세요. 키가 채팅에 노출되지 않고, 결과도 본인에게만 보입니다.\n" +
        "(입력창에 `/인증` 입력 → `키` 칸에 라이센스 키 붙여넣기)"
    );
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

  // !패널 — 관리자: 현재 채널에 인증/등수 버튼 패널 게시
  if (cmd === "패널" || cmd === "panel") {
    if (!msg.guild || !msg.member) return;
    if (!msg.member.permissions.has(PermissionFlagsBits.Administrator)) {
      return safeReply(msg, "❌ 관리자만 사용할 수 있습니다.");
    }
    try {
      await msg.channel.send(buildPanel());
      await msg.delete().catch(() => {});
    } catch (e) {
      console.error("패널 게시 실패:", e?.message || e);
      return safeReply(msg, "❌ 패널 게시 실패. 봇에 '메시지 보내기'/'임베드 링크' 권한이 있는지 확인하세요.");
    }
    return;
  }

  // !등수 — 현재 등수 조회
  if (cmd === "등수" || cmd === "rank") {
    return safeReply(msg, await rankText(msg.author.id));
  }

  // !도움
  if (cmd === "도움" || cmd === "help") {
    return safeReply(msg, HELP_TEXT);
  }
});

process.on("unhandledRejection", (e) => {
  console.error("unhandledRejection:", e?.message || e);
});

// ─── 만료 임박 DM 알림 ───
async function runExpiryAlerts() {
  try {
    const data = await apiGet(`${EXPIRING_API}?days=${EXPIRY_ALERT_DAYS}`);
    if (!data.success || !Array.isArray(data.list)) return;
    let sent = 0;
    for (const item of data.list) {
      try {
        const user = await client.users.fetch(item.discordId);
        await user.send(
          `⏰ **라이센스 만료 임박 안내**\n` +
            `회원님의 라이센스가 **${item.remainingDays}일 후 만료**됩니다.\n` +
            `계속 사용하시려면 \`#구매방법\` 채널에서 연장해 주세요. 🙏`
        );
        sent++;
      } catch (e) {
        // DM 차단/탈퇴 등은 무시
      }
    }
    if (sent) console.log(`만료 임박 DM 발송: ${sent}건`);
  } catch (e) {
    console.error("만료 알림 오류:", e?.message || e);
  }
}

client.once("ready", async () => {
  console.log(`✅ 봇 로그인: ${client.user.tag}`);
  // 각 서버에 슬래시 명령 등록(길드 명령은 즉시 반영).
  for (const [, guild] of client.guilds.cache) {
    await registerGuildCommands(guild);
  }
  // 만료 임박 알림: 시작 1분 뒤 1회 + 매일.
  setTimeout(runExpiryAlerts, 60000);
  setInterval(runExpiryAlerts, 24 * 3600 * 1000);
});
// 새 서버에 초대되면 슬래시 명령 자동 등록.
client.on("guildCreate", registerGuildCommands);

if (!DISCORD_TOKEN) {
  console.error("❌ DISCORD_TOKEN 환경변수가 설정되지 않았습니다.");
  process.exit(1);
}
if (!BOT_API_KEY) {
  console.error("❌ BOT_API_KEY 환경변수가 설정되지 않았습니다.");
  process.exit(1);
}

client.login(DISCORD_TOKEN);
