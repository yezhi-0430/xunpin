"use strict";
const state = {
  mode: "live",
  page: window.location.hash === "#history" ? "history" : "overview",
  data: null,
  filter: "all",
  busy: false,
  history: null,
  historyOffset: 0,
};
const $ = (id) => document.getElementById(id);
const esc = (value) =>
  String(value ?? "").replace(
    /[&<>"']/g,
    (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        c
      ],
  );
const money = (cents) =>
  cents === null || cents === undefined
    ? "待核实"
    : `¥${(cents / 100).toFixed(2)}`;
const date = (value) =>
  value
    ? new Date(typeof value === "number" ? value * 1000 : value).toLocaleString(
        "zh-CN",
        {
          timeZone: "Asia/Shanghai",
          month: "2-digit",
          day: "2-digit",
          hour: "2-digit",
          minute: "2-digit",
          hour12: false,
        },
      )
    : "尚未执行";
const kinds = {
  price: "价格",
  promotion: "促销",
  title: "标题",
  description: "描述",
  reviews: "评价",
  availability: "覆盖",
};
const icons = {
  price: "↘",
  promotion: "◇",
  title: "T",
  description: "▤",
  reviews: "≋",
  availability: "!",
};
const statuses = {
  unconfigured: "模型未配置",
  restricted: "外发未获准",
  failed: "分析未通过",
  disabled: "未配置",
  expired: "许可过期",
  ready: "待运行",
  ok: "运行正常",
  paused: "已暂停",
  backoff: "退避等待",
  cooldown: "冷却中",
  budget: "预算用尽",
  cached: "共享缓存",
  pending: "待处理",
  accepted: "已采纳",
  rejected: "已驳回",
  executed: "已执行",
  evaluated: "已评估",
};
const pages = {
  history: ["真实历史样本", "从真实文案，验证内容理解", "天池官方公开历史文本 · 保留来源和原文，不推断当前市场状态。"],
  overview: [
    "监控概览",
    "商品监控工作台",
    "跟踪关键变化，让下一步运营动作有据可依。",
  ],
  events: [
    "变化事件",
    "发现值得关注的变化",
    "从前后快照出发，区分事实、推测与待核实信息。",
  ],
  tasks: [
    "运营任务",
    "让建议进入处理流程",
    "明确动作、负责人和观察指标，记录每一次运营判断。",
  ],
  sources: [
    "数据与风控",
    "来源可信，运行有界",
    "查看字段覆盖、调用预算、异常暂停与恢复条件。",
  ],
};
function toast(text) {
  $("toast").textContent = text;
  $("toast").classList.add("show");
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => $("toast").classList.remove("show"), 4000);
}
async function api(path, body) {
  const r = await fetch(
    `/api${path}`,
    body === undefined
      ? {}
      : {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        },
  );
  const data = await r.json();
  if (!r.ok)
    throw new Error(
      typeof data.detail === "string"
        ? data.detail
        : Array.isArray(data.detail)
          ? data.detail.map((error) => error.msg.replace(/^Value error, /, "")).join("；")
          : "请求未通过，请检查输入",
    );
  return data;
}
async function refresh() {
  const mode = state.mode;
  try {
    const data = await api(mode === "live" ? "/dashboard" : `/overview?mode=${mode}`);
    if (mode !== state.mode) return;
    const changed =
      JSON.stringify(data, (k, v) => (k === "age_seconds" ? undefined : v)) !==
      JSON.stringify(state.data, (k, v) =>
        k === "age_seconds" ? undefined : v,
      );
    state.data = data;
    if (state.page === "history") await loadHistory();
    else if (changed) render();
  } catch (e) {
    toast(e.message);
    if (!state.data)
      $("content").innerHTML =
        '<div class="empty">读取失败，请确认服务正在运行。</div>';
  }
}
async function act(fn) {
  if (state.busy) return;
  state.busy = true;
  document.querySelectorAll("button").forEach((b) => (b.disabled = true));
  try {
    const result = await fn();
    if (result?.message) toast(result.message);
    await refresh();
  } catch (e) {
    toast(e.message);
  } finally {
    state.busy = false;
    document.querySelectorAll("button").forEach((b) => (b.disabled = false));
  }
}
function tag(label, cls = "") {
  return `<span class="tag ${cls}">${esc(label)}</span>`;
}
function empty(title, description, action = "") {
  return `<div class="empty"><div class="empty-symbol">◫</div><h3>${esc(title)}</h3><p>${esc(description)}</p>${action}</div>`;
}
function panel(title, subtitle, body, link = "") {
  return `<section class="panel"><div class="panel-head"><div><h2>${title}</h2>${subtitle ? `<p>${subtitle}</p>` : ""}</div>${link}</div>${body}</section>`;
}
function setPage(page) {
  state.page = page;
  window.history.replaceState(null, "", page === "history" ? "#history" : window.location.pathname);
  state.filter = "all";
  render();
  if (page === "history") loadHistory();
}

async function loadHistory() {
  const offset = state.historyOffset;
  try {
    const data = await api(`/history?offset=${offset}&limit=20`);
    if (state.page !== "history" || offset !== state.historyOffset) return;
    state.history = data;
    render();
  } catch (e) { toast(e.message); }
}

function historicalSamples() {
  const d = state.history;
  if (!d) return empty("正在读取历史样本", "读取本地已导入数据，不请求淘宝。");
  if (!d.dataset) return empty("尚未导入历史样本", "从本地获准的天池标题/描述文件导入后展示，不随机生成内容。");
  const m = d.dataset;
  return panel("天池 · 商品描述文案", "真实历史文本记录，不等同于当前商品或连续快照", `<div class="history-meta"><p><strong>${d.total} 条抽样文案</strong> · 全量扫描 ${m.total_rows.toLocaleString("zh-CN")} 行 · 有效 ${m.valid_rows.toLocaleString("zh-CN")} 行 · 跳过 ${m.skipped_rows.toLocaleString("zh-CN")} 行</p><p>来源文件日期：${esc(m.source_file_date)} · 本地导入：${date(m.imported_at)}<br>采样：固定种子 ${m.seed}，按有效记录抽样后去重；不能视为独立标注或全部商品覆盖。</p><p>可用：标题、描述。未提供：淘宝商品ID、价格、促销、评价、原始商品观测时间；文案中的宣传不代表已经核实的产品事实。</p><p class="subtle">${esc(m.credit)}<br>${esc(m.license_note)}<br>文件 SHA-256：${esc(m.archive_sha256)}</p><a class="text-button" href="https://tianchi.aliyun.com/dataset/9717" target="_blank" rel="noopener noreferrer">查看官方来源 ↗</a></div>`) + `<div class="page-toolbar"><span class="subtle">第 ${state.historyOffset + 1}–${Math.min(state.historyOffset + d.samples.length, d.total)} 条 / 共 ${d.total} 条样本文案</span><div class="actions"><button class="secondary" data-action="history-page" data-offset="${Math.max(0, state.historyOffset - 20)}" ${state.historyOffset === 0 ? "disabled" : ""}>上一页</button><button class="secondary" data-action="history-page" data-offset="${state.historyOffset + 20}" ${state.historyOffset + 20 >= d.total ? "disabled" : ""}>下一页</button></div></div><div class="task-grid">${d.samples.map(s => `<article class="task history-card"><div class="task-top">${tag("真实历史文案", "gray")}${s.last_attempt_status ? tag("最近重试未通过", "amber") : ""}${tag(s.analysis_status === "ok" ? "AI 已解读" : s.analysis_status === "failed" ? "AI 分析未通过" : s.analysis_status === "restricted" ? "外发未获准" : "原文可查", "gray")}</div><h3>${esc(s.title)}</h3><p>${esc(s.description_preview)}${s.description_preview.length >= 160 ? "…" : ""}</p><p class="subtle">原始文件第 ${s.line_number.toLocaleString("zh-CN")} 行 · 没有当前报价</p><div class="actions"><button class="secondary" data-action="history-detail" data-id="${esc(s.id)}">查看原文 / AI 解读 ↗</button></div></article>`).join("")}</div>`;
}

async function showHistoricalSample(id) {
  try {
    const s = await api(`/history/${id}`);
    $("dialog-kicker").textContent = "真实历史样本 / 标题与描述";
    $("dialog-body").innerHTML = `<h2>${esc(s.title)}</h2><p class="subtle">天池9717 · 原始文件第 ${s.line_number.toLocaleString("zh-CN")} 行 · 历史文案，非当前观测</p><div class="evidence-facts">这是一份原始文案记录，不包含前后版本；价格、促销、评价与原始商品时间未提供。</div><section class="version history-original"><div class="version-label">原始描述 · 来源文本中的商品宣传需核实</div><p>${esc(s.description)}</p></section><p class="subtle history-provenance">${esc(s.dataset.credit)}<br>${esc(s.dataset.license_note)}<br>ZIP SHA-256：${esc(s.archive_sha256)}<br>样本记录号（非淘宝商品ID）：${esc(s.id)}</p><div class="actions"><button class="primary" data-action="history-analyze" data-id="${esc(s.id)}" ${!s.dataset.ai_allowed ? "disabled" : ""}>分析此历史样本</button><span class="subtle">一次模型请求 · 仅发送当前标题和描述 · 不推断前后变化</span></div><div id="analysis-result"></div>`;
    if (s.analysis) renderHistoricalAnalysis(s.analysis);
    $("detail-dialog").showModal();
  } catch (e) { toast(e.message); }
}
function renderHistoricalAnalysis(analysis) {
  renderAnalysis(analysis, "真实历史文案 / 天池9717");
  if (analysis.last_attempt) {
    const notice = document.createElement("p");
    notice.className = "evidence-facts";
    notice.textContent = `最近一次尝试未通过，仍展示此前成功解读。${analysis.last_attempt.limitations || ""}`;
    $("analysis-result").prepend(notice);
  }
}
function metric(label, value, unit, note, warm = false) {
  return `<div class="metric ${warm ? "warm" : ""}"><div class="metric-top">${label}<span class="metric-icon">${warm ? "↗" : "◫"}</span></div><div class="metric-value">${value}<small>${unit}</small></div><div class="metric-detail">${note}</div></div>`;
}
function sourceBrief() {
  return (
    state.data.sources
      .map(
        (s) =>
          `<div class="source-brief"><div class="source-icon">${s.kind === "official" ? "淘" : "源"}</div><div><strong>${s.kind === "official" ? "官方卖家授权" : "竞品授权数据服务"}</strong><p>${s.kind === "official" ? "仅配置的自店商品" : "以来源许可和实际覆盖为准"}</p></div>${tag(statuses[s.status] || s.status, s.status === "ok" ? "" : "amber")}</div>`,
      )
      .join("") || '<p class="subtle">尚未配置正式来源。</p>'
  );
}
function scenarioPanel() {
  const d = state.data;
  return panel(
    "场景回放",
    "固定构造样例 · 不连接淘宝 · 不代表真实采集结果",
    `<div class="scenario-track">${d.scenarios.map((s, i) => `<button class="scenario ${d.replay_step === i ? "current" : d.replay_step > i ? "done" : ""}" data-action="scenario" data-id="${s.id}"><span>${d.replay_step >= i ? "✓" : "0" + (i + 1)}</span><strong>${esc(s.title)}</strong><small>${esc(s.detail)}</small></button>`).join("")}</div><div class="scenario-footer"><p>逐步验证真实降价、优惠抵消与内容变化。</p><div class="actions"><button class="text-button" data-action="reset">重置回放</button><button class="primary" data-action="next">${d.replay_step < 0 ? "建立监控基线" : d.replay_step < 3 ? "推进下一场景 →" : "重复检查 · 验证去重"}</button></div></div>`,
  );
}
function factsText(e) {
  const f = e.facts;
  if (e.kind === "price")
    return `${money(f.before_cents)} → ${money(f.after_cents)} · ${esc(f.spec)}`;
  if (e.kind === "promotion")
    return f.payable_unchanged
      ? "到手价未变 · 不触发降价告警"
      : "优惠条件调整 · 先核验购买条件";
  if (e.kind === "reviews")
    return `新增 ${f.sample_count} 条样本 · 不推断全体用户`;
  if (e.kind === "availability") return "缺失字段保留未知，不解释为下架";
  return "保留前后正文 · 等待核查业务影响";
}
function eventRows(events) {
  if (!events.length)
    return empty(
      "暂无变化事件",
      state.mode === "live"
        ? "配置来源并建立基线后，重要变化会出现在这里。"
        : "建立基线并推进场景，查看有证据的变化。",
    );
  return events
    .map(
      (e) =>
        `<div class="event-row"><div class="event-symbol ${e.kind}">${icons[e.kind]}</div><div class="event-main"><h3>${esc(e.title)}</h3><p>${esc(e.product_title)} · ${date(e.created_at)}</p><div class="event-fact">${factsText(e)}</div></div><div class="event-meta">${tag(kinds[e.kind], e.priority === "high" ? "red" : "gray")}<button class="text-button" data-action="evidence" data-id="${e.id}">查看证据 ↗</button></div></div>`,
    )
    .join("");
}
function prices() {
  const all = state.data.products;
  const known = all.filter(
    (p) => p.payable_cents !== null && p.spec && p.conditions,
  );
  const reference = known.find((p) => p.role === "own") || known[0];
  const products = reference
    ? known.filter(
        (p) =>
          p.spec === reference.spec && p.conditions === reference.conditions,
      )
    : [];
  if (!products.length)
    return empty(
      "等待可比商品报价",
      "报价需在有效期内，并且规格与购买条件明确；过期或未知数据不进入当前对照。",
    );
  const max = Math.max(...products.map((p) => p.payable_cents), 1);
  return `<div class="price-list">${products.map((p) => `<div class="price-line ${p.role === "own" ? "own" : ""}"><div class="price-line-head"><span>${esc(p.title)}</span><strong>${money(p.payable_cents)}</strong></div><div class="price-track"><i style="width:${(p.payable_cents / max) * 100}%"></i></div></div>`).join("")}<p class="subtle">${esc(reference.spec)} · ${esc(reference.conditions)}<br>仅对照相同规格与购买条件；${all.length - products.length} 件因未知、过期或条件不同未纳入。</p></div>`;
}
function overview() {
  const d = state.data,
    pending = d.tasks.filter((t) => t.status === "pending").length;
  return `${state.mode === "replay" ? scenarioPanel() : ""}<div class="metrics">${metric("监控商品", d.products.length, "件", `${d.products.filter((p) => p.role === "own").length} 件本店 / ${d.products.filter((p) => p.role === "competitor").length} 件竞品`)}${metric("关键变化", d.events.length, "项", `${d.events.filter((e) => e.priority === "high").length} 项高优先级事件`, true)}${metric("待处理建议", pending, "项", `${d.tasks.filter((t) => t.status !== "pending").length} 项已有人工处理记录`)}${metric(state.mode === "live" ? "正常来源" : "样例进度", state.mode === "live" ? d.sources.filter((s) => s.status === "ok").length : Math.max(0, d.replay_step + 1), state.mode === "live" ? "个" : "/ 4", state.mode === "live" ? "来源许可与字段覆盖须核验" : "全部使用固定构造样例")}</div><div class="grid-two">${panel("最近变化", "事件按观测顺序排列", eventRows(d.events.slice(0, 4)), '<button class="text-button" data-page="events">全部事件 →</button>')}${panel("可比报价对照", "当前快照，不构造历史曲线", prices(), tag(state.mode === "replay" ? "构造样例" : "真实观测", "gray"))}</div>${state.mode === "live" ? `<div class="grid-two">${panel("接入准备", "启用前明确来源、范围与实际字段", `<div class="panel-body setup-flow"><div class="setup-step"><span class="step-index">01</span><h3>确认数据来源</h3><p>自店使用官方授权能力，竞品另核实来源与监控用途。</p></div><div class="setup-step"><span class="step-index">02</span><h3>核验字段覆盖</h3><p>验证标题、描述、价格、促销及评价的实际可用范围。</p></div><div class="setup-step"><span class="step-index">03</span><h3>按计划持续监测</h3><p>配置配额后定时获取，异常自动退避或暂停。</p></div></div>`)}${panel("数据来源状态", "凭证仅保存在本地", `<div class="panel-body">${sourceBrief()}<hr class="divider"><div class="mini-kv"><span>AI 分析</span><strong>${d.ai.configured ? "已配置 · 需人工触发" : "未配置 · 显示规则草稿"}</strong></div></div>`, '<button class="text-button" data-page="sources">查看 →</button>')}</div>` : panel("监控商品", "每件商品绑定固定身份、来源与快照", productTable())}`;
}
function productTable() {
  const rows = state.data.products;
  return rows.length
    ? `<div class="table-wrap"><table><thead><tr><th>商品</th><th>规格</th><th>标价 / 优惠</th><th>到手价</th><th>内容覆盖</th><th>最近检查</th></tr></thead><tbody>${rows
        .map(
          (p) =>
            `<tr><td><strong>${esc(p.title)}</strong><small>${p.role === "own" ? "本店" : "竞品"} · ${esc(p.product_id)} · ${esc(p.source_id)}</small></td><td>${esc(p.spec || "未提供")}</td><td>${money(p.price_cents)} / ${money(p.coupon_cents)}</td><td><strong>${money(p.payable_cents)}</strong>${p.freshness === "stale" ? "<small>历史报价已过期</small>" : p.freshness === "restricted" ? "<small>来源或范围待核验</small>" : ""}</td><td><small>${[
              ["标题", p.title],
              ["描述", p.description],
              ["价格", p.price_cents],
              ["优惠", p.coupon_cents],
              ["券门槛", p.coupon_threshold_cents],
              ["价格确认", p.price_confirmed === true ? true : null],
              ["评价", p.reviews],
            ]
              .map(
                ([label, value]) =>
                  `${label}${value === null || value === undefined ? "待获取" : "✓"}`,
              )
              .join(" · ")}</small></td><td>${date(p.checked_at)}</td></tr>`,
        )
        .join("")}</tbody></table></div>`
    : empty(
        "暂无已获取商品",
        "未配置或未通过来源验证时，正式商品列表保持为空。",
      );
}
function events() {
  const list =
    state.filter === "all"
      ? state.data.events
      : state.data.events.filter((e) => e.kind === state.filter);
  return `<div class="page-toolbar"><h2>变化事件 <span class="subtle">/ ${list.length} 项</span></h2><div class="filter-group">${[["all", "全部"], ...Object.entries(kinds)].map(([key, label]) => `<button class="${state.filter === key ? "selected" : ""}" data-filter="${key}">${label}</button>`).join("")}</div></div>${panel("变化与证据", "点击查看原始快照和确定性计算结果", eventRows(list))}`;
}
function taskCards() {
  const tasks = state.data.tasks;
  if (!tasks.length)
    return panel(
      "运营建议",
      "事件出现后自动建立规则草稿",
      empty("暂无待处理任务", "建议随有证据的变化产生，不预先填充通用建议。"),
    );
  return `<div class="task-grid">${tasks.map((t) => `<article class="task"><div class="task-top">${tag(statuses[t.status] || t.status, t.status === "pending" ? "amber" : "")}${tag(t.analysis?.engine === "llm" ? "AI 已解读" : "规则草稿", "gray")}</div><h3>${esc(t.title)}</h3><p>${esc(t.action)}</p><div class="subtle">${esc(t.owner)} · 观测商品 ${esc(t.observed_product_id || t.product_id)}<br>目标本店商品：${esc(t.target_product_ids?.join("、") || "待运营指定")} · 执行前核实</div><div class="task-metric">观察指标：${esc(t.metric)}<br>前提：${esc(t.analysis?.engine === "llm" ? "规则任务已附 AI 解读；执行前需人工核实，不提供收益预测。" : t.limitation)}</div>${t.brief ? `<details class="task-brief"><summary>查看美工需求单</summary><p>目标：${esc(t.brief.objective)}<br>交付：${esc(t.brief.deliverable)}<br>文案：${esc(t.brief.copy_requirement)}</p><p>画面要素：${t.brief.visual_elements.map(esc).join("；")}<br>验收：${t.brief.acceptance.map(esc).join("；")}</p></details>` : ""}${t.note ? `<p>处理记录：${esc(t.note)}</p>` : ""}${executionSummary(t.execution)}<div class="actions"><button class="secondary" data-action="feedback" data-id="${t.id}">记录处理</button><button class="text-button" data-action="evidence" data-id="${t.event_id}">证据 / AI 解读 ↗</button></div></article>`).join("")}</div>`;
}
function sources() {
  const d = state.data;
  return `<div class="source-grid">${d.sources.map((s) => `<article class="source-card"><div class="source-top"><h3>${s.kind === "official" ? "官方卖家授权接口" : "竞品授权数据服务"}</h3>${tag(statuses[s.status] || s.status, s.status === "ok" ? "" : "amber")}</div><p>${s.kind === "official" ? "仅用于配置范围内的自店商品；标价不等于活动到手价。" : "按已获许可的商品范围与字段契约获取；服务来源需另行核验。"}</p><div class="mini-kv"><span>监控范围</span><strong>${s.product_count} 件商品</strong></div><div class="mini-kv"><span>检查周期 / 每日预算</span><strong>${s.interval_seconds / 60} 分钟 / ${s.daily_budget} 次</strong></div><div class="mini-kv"><span>今日调用 / 最近成功</span><strong>${s.requests_today} 次 / ${date(s.last_success)}</strong></div><hr class="divider"><p>${esc(s.reason)}</p><div class="subtle">${esc(s.permission_note)}<br>${esc(s.validation)}</div>${state.mode === "live" ? `<div class="actions"><button class="secondary" data-action="collect" data-id="${s.source_id}">检查一次</button><button class="text-button" data-action="pause" data-id="${s.source_id}">暂停</button><button class="text-button" data-action="resume" data-id="${s.source_id}">人工恢复</button></div>` : '<p class="subtle">正式来源状态仅供查看；回放不能操作正式采集。</p>'}</article>`).join("")}</div><div style="height:21px"></div>${panel("已观测字段与商品", "缺失信息保持未知，不自动补全", productTable())}${state.mode === "replay" ? panel("异常处理验证", "故障注入只影响回放状态，不发起真实请求", `<div class="panel-body"><div class="risk-state"><span>${esc(d.risk.reason)}</span>${tag(statuses[d.risk.status] || d.risk.status, "amber")}</div><div class="actions"><button class="secondary" data-action="fault" data-id="rate">注入 429 限流</button><button class="secondary" data-action="fault" data-id="auth">注入 403 权限失效</button><button class="secondary" data-action="fault" data-id="schema">注入 HTML 异常</button><button class="text-button" data-action="fault" data-id="resume">恢复回放</button></div><p class="subtle">真实采集控制器由接口契约测试验证；这里用于演示状态变化。</p></div>`) : panel("采集保护机制", "所有正式来源统一经过控制层", `<div class="panel-body setup-flow"><div class="setup-step"><h3>频率与重复请求</h3><p>独立限流与每日预算；同周期共享快照，执行互斥。</p></div><div class="setup-step"><h3>异常响应识别</h3><p>识别 HTTP 与业务错误、HTML、结构变化和过期数据。</p></div><div class="setup-step"><h3>自动暂停与恢复</h3><p>权限失效立即暂停；传输故障冷却探测，记录恢复条件。</p></div></div>`)}${panel("运行记录", "最近的采集、回放和异常轨迹", d.runs.length ? d.runs.map((r) => `<div class="run-row"><span>${date(r.created)}</span><span>${esc(statuses[r.status] || r.status)}</span><span class="run-text">${esc(r.message)}</span></div>`).join("") : empty("暂无运行记录", "调度、手动检查或回放操作后将在这里保留轨迹。"))}`;
}
function render() {
  if (!state.data) return;
  const [crumb, title, desc] = pages[state.page];
  $("page-crumb").textContent = crumb;
  $("page-title").textContent = title;
  $("page-description").textContent = desc;
  document
    .querySelectorAll("[data-page].nav-item")
    .forEach((b) =>
      b.classList.toggle("active", b.dataset.page === state.page),
    );
  document
    .querySelectorAll("[data-mode]")
    .forEach((b) =>
      b.classList.toggle("selected", b.dataset.mode === state.mode),
    );
  $("nav-event-count").textContent = state.data.events.length;
  $("nav-task-count").textContent = state.data.tasks.filter(
    (t) => t.status === "pending",
  ).length;
  $("mode-notice").className =
    "notice " + (state.mode === "replay" ? "replay" : "");
  document.querySelector(".mode-control").style.display = state.page === "history" ? "none" : "flex";
  $("mode-notice").innerHTML =
    state.page === "history"
      ? '<span class="notice-label">真实历史样本</span><span><strong>来自天池公开数据。</strong> 仅验证文本理解；本地导入时间不代表商品更新时间，不生成变化告警。</span>'
      : state.data.imported_examples
      ? '<span class="notice-label">当前数据</span><span>已导入 Claude 生成的商品、告警和建议；在线淘宝接口尚未接通。</span>'
      : state.mode === "replay"
      ? '<span class="notice-label">流程回放</span><span><strong>当前为构造样例。</strong> 用于验证处理逻辑；未连接淘宝，不代表完成真实采集或实际经营效果。</span>'
      : '<span class="notice-label">正式采集</span><span><strong>接入状态以实际来源为准。</strong> 未配置权限时保持停用；可在流程回放中检查业务与异常处理。</span>';
  const renderers = state.data.imported_examples ? {overview: simulationOverview, events: simulationEvents, tasks: simulationTasks, sources: simulationSources, history: historicalSamples} : {overview, events, tasks: taskCards, sources, history: historicalSamples};
  $("content").innerHTML = renderers[
    state.page
  ]();
}
function versionHtml(s, label, after) {
  return `<section class="version ${after ? "after" : ""}"><div class="version-label">${label} · ${date(s.observed_at)}</div><h4>${esc(s.title)}</h4><p>${esc(s.description ?? "描述未提供")}<br>规格：${esc(s.spec ?? "未知")}<br>条件：${esc(s.conditions ?? "未知")}<br>标价：${money(s.price_cents)} · 优惠：${money(s.coupon_cents)}<br>券门槛：${money(s.coupon_threshold_cents)} · 价格条件：${s.price_confirmed === true ? "已确认" : s.price_confirmed === false ? "未确认" : "未知"}</p><pre>${esc(s.reviews === null ? "评价未提供" : (s.reviews || []).map((r) => `${r.review_id}：${r.text}`).join("\n"))}</pre></section>`;
}
async function showEvidence(id) {
  try {
    const d = await api(`/events/${id}?mode=${state.mode}`);
    $("dialog-kicker").textContent = `事件证据 / ${kinds[d.event.kind]}`;
    $("dialog-body").innerHTML =
      `<h2>${esc(d.event.title)}</h2><div class="subtle">${esc(d.event.product_title)} · ${esc(d.event.source_id)} · ${state.mode === "replay" ? "构造样例" : "正式采集"}</div><p class="evidence-link">证据 ID：${esc(id)} · 快照 ${d.event.before_snapshot_id} → ${d.event.after_snapshot_id}</p><div class="evidence-facts">${factsText(d.event)}</div><div class="evidence-versions">${versionHtml(d.before, "变化前", false)}${versionHtml(d.after, "变化后", true)}</div><div class="actions"><button class="primary" data-action="analyze" data-id="${id}">${state.data.ai.configured ? "AI 解读此事件" : "检查 AI 分析状态"}</button><span class="subtle">${state.data.ai.configured ? "至多一次只读工具核验，结果需人工审核" : "未配置模型，不会发起模型请求"}</span></div><div id="analysis-result"></div>`;
    const saved = state.data.tasks.find((t) => t.event_id === id)?.analysis;
    if (saved) renderAnalysis(saved);
    $("detail-dialog").showModal();
  } catch (e) {
    toast(e.message);
  }
}
function renderAnalysis(result, inputLabel = state.mode === "replay" ? "构造样例" : "已获准的事件证据") {
  const categories = { selling_point: "卖点", use_case: "使用场景", service_promise: "服务承诺", promotion_mechanism: "促销机制", review_theme: "评价主题" };
  const findings = (result.findings || []).map((finding) => `<section class="semantic-finding"><strong>${esc(categories[finding.category] || finding.category)}</strong><p>${esc(finding.observation)}</p>${finding.citations.map((citation) => `<blockquote>${esc(citation.quote)}<small>${citation.snapshot === "sample" ? "历史原文" : citation.snapshot === "before" ? "变化前" : "变化后"} · ${esc(citation.field)}${citation.review_id ? ` · ${esc(citation.review_id)}` : ""}</small></blockquote>`).join("")}${finding.category === "review_theme" ? `<p class="subtle">引用 ${finding.cited_review_count} 条 / 本事件新增 ${result.review_sample_total} 条；引用数不代表主题总体占比。</p>` : ""}</section>`).join("");
  $("analysis-result").innerHTML =
    `<div class="analysis-box"><strong>${result.engine === "llm" ? "AI 解读" : "规则结果"} · ${esc(statuses[result.status] || result.status)}</strong>${result.engine === "llm" ? `<p class="subtle">模型：${esc(result.model)} · 输入：${esc(inputLabel)}</p>` : ""}<p>${esc(result.summary)}</p>${findings || `<p class="subtle">${result.engine === "llm" ? "本次未形成可引用的语义发现。" : "没有已验证的模型语义发现。"}</p>`}${result.hypotheses?.length ? `<h3>谨慎推测 · 待核实</h3><ul>${result.hypotheses.map((value) => `<li>${esc(value)}</li>`).join("")}</ul>` : ""}${result.recommendations?.length ? `<h3>行动建议 · 需人工审核</h3><ul>${result.recommendations.map((r) => `<li>${esc(r.action)} · ${esc(r.owner)}<br>条件：${esc(r.conditions)}；观察：${esc(r.metric)}${r.evaluation_plan ? `<br>评估：${esc(r.evaluation_plan)}` : ""}</li>`).join("")}</ul>` : ""}<p>${esc(result.limitations)}${result.evidence_truncated ? " 本次输入有节选，未覆盖全部正文或样本。" : ""}</p><div class="evidence-link">引用：${result.evidence_ids.map(esc).join(" / ")}</div>${result.trace?.length ? `<p>只读核验：${result.trace.map((t) => esc(t.tool)).join(" → ")}</p>` : ""}</div>`;
}

const evaluationStatuses = { pending: "待观察", observed: "已记录观察结果", inconclusive: "暂时无法评估" };
function executionSummary(record) {
  if (!record) return "";
  return `<div class="execution-summary"><strong>效果复盘 · ${esc(evaluationStatuses[record.evaluation_status])}</strong>${record.planned_date ? `<p>计划完成：${esc(record.planned_date)}</p>` : ""}${record.actual_action ? `<p>实际动作：${esc(record.actual_action)}</p>` : ""}${record.evaluation_status === "observed" ? `<p>${esc(record.metric_name)}：${esc(record.baseline_value)} → ${esc(record.outcome_value)} ${esc(record.metric_unit)}<br>观察窗口：${esc(record.observation_start)} 至 ${esc(record.observation_end)}<br>数据来源：${esc(record.data_reference)}</p><p class="subtle">仅为人工录入观察，尚不能证明动作带来因果改善。</p>` : ""}${record.evaluation_note ? `<p>${esc(record.evaluation_note)}</p>` : ""}${record.confounders ? `<p>干扰因素：${esc(record.confounders)}</p>` : ""}</div>`;
}
function executionForm(record = {}) {
  const input = (key, label, type = "text", max = 150) => `<div><label for="execution-${key}">${label}</label><input id="execution-${key}" type="${type}" ${type === "number" ? 'step="any"' : `maxlength="${max}"`} value="${esc(record[key] ?? "")}"></div>`;
  return `<details class="execution-editor" ${record.evaluation_status ? "open" : ""}><summary>执行计划与效果复盘</summary><p class="subtle">没有数据时选择“暂时无法评估”，数字留空；0 表示实际测得为零。</p><div class="execution-fields">${input("planned_date", "计划完成日期", "date")}${input("observation_start", "观察开始日期", "date")}${input("observation_end", "观察结束日期", "date")}${input("actual_action", "实际执行的动作", "text", 1500)}${input("metric_name", "观察指标")}${input("metric_unit", "指标单位", "text", 30)}${input("baseline_value", "执行前基线", "number")}${input("outcome_value", "观察结果值", "number")}${input("data_reference", "数据来源 / 台账位置", "text", 500)}${input("confounders", "同期活动等干扰因素", "text", 1000)}<div><label for="execution-evaluation_status">评估状态</label><select id="execution-evaluation_status">${Object.entries(evaluationStatuses).map(([key, label]) => `<option value="${key}" ${record.evaluation_status === key ? "selected" : ""}>${label}</option>`).join("")}</select></div>${input("evaluation_note", "评估说明 / 无法判断的原因", "text", 1000)}</div></details>`;
}
function readExecutionForm() {
  const record = {};
  for (const key of ["planned_date", "observation_start", "observation_end", "actual_action", "metric_name", "metric_unit", "baseline_value", "outcome_value", "data_reference", "confounders", "evaluation_status", "evaluation_note"]) {
    const value = $("execution-" + key).value.trim();
    record[key] = ["planned_date", "observation_start", "observation_end"].includes(key)
      ? value || null
      : ["baseline_value", "outcome_value"].includes(key)
        ? value === "" ? null : Number(value)
        : value;
  }
  return record;
}
function showFeedback(id) {
  const task = state.data.tasks.find((t) => t.id === id);
  $("dialog-kicker").textContent = "运营处理记录";
  $("dialog-body").innerHTML =
    `<h2>${esc(task.title)}</h2><p class="subtle">处理理由会与证据一起保存，便于后续评估。</p><label for="task-status">处理状态</label><select id="task-status">${["pending", "accepted", "rejected", "executed", "evaluated"].map((s) => `<option value="${s}" ${task.status === s ? "selected" : ""}>${statuses[s]}</option>`).join("")}</select><label for="task-note">处理理由 / 执行结果</label><textarea id="task-note" maxlength="1000" placeholder="例如：先核实毛利，再测试限时券。">${esc(task.note)}</textarea>${executionForm(task.execution)}<div class="actions" style="margin-top:18px"><button class="primary" data-action="save-feedback" data-id="${id}">保存处理记录</button></div>${task.history.length ? `<h3>历史记录</h3>${task.history.map((h) => `<p class="subtle">${date(h.at)} · ${statuses[h.status]} · ${esc(h.note)}</p>${executionSummary(h.execution)}`).join("")}` : ""}`;
  $("detail-dialog").showModal();
}
document.addEventListener("click", async (e) => {
  const b = e.target.closest("button");
  if (!b) return;
  if (b.dataset.page) {
    setPage(b.dataset.page);
    return;
  }
  if (b.dataset.mode) {
    state.mode = b.dataset.mode;
    state.filter = "all";
    await refresh();
    return;
  }
  if (b.dataset.filter) {
    state.filter = b.dataset.filter;
    render();
    return;
  }
  const action = b.dataset.action,
    id = b.dataset.id;
  if (action === "simulation-page") { simulationOffset = Number(b.dataset.offset); render(); return; }
  if (action === "simulation-detail") { showSimulationDetail(id); return; }
  if (action === "history-page") {
    const offset = Number(b.dataset.offset);
    if (offset < 0 || offset >= (state.history?.total || 0)) return;
    state.historyOffset = offset;
    await loadHistory();
    return;
  }
  if (action === "history-detail") { await showHistoricalSample(id); return; }
  if (action === "history-analyze") {
    await act(async () => {
      const r = await api(`/history/${id}/analyze`, {});
      renderHistoricalAnalysis(r.saved_analysis || r);
      return {message:r.preserved_previous ? "本次未通过，已保留此前成功解读" : r.engine === "llm" ? "真实历史文本解读已保存" : "已显示分析状态，未发布模型结论"};
    });
    return;
  }
  if (action === "evidence") {
    await showEvidence(id);
    return;
  }
  if (action === "feedback") {
    showFeedback(id);
    return;
  }
  if (action === "analyze") {
    await act(async () => {
      const r = await api(`/events/${id}/analyze`, { mode: state.mode });
      renderAnalysis(r);
      return {
        message: r.engine === "llm" ? "AI 解读已保存" : "已显示当前分析状态",
      };
    });
    return;
  }
  if (action === "save-feedback") {
    const note = $("task-note").value.trim();
    if (!note) {
      toast("请填写处理理由或结果");
      return;
    }
    await act(async () => {
      await api(`/tasks/${id}/feedback`, {
        mode: state.mode,
        status: $("task-status").value,
        note,
        execution: readExecutionForm(),
      });
      $("detail-dialog").close();
      return { message: "处理记录已保存" };
    });
    return;
  }
  if (action === "reset") {
    await act(() => api("/replay/reset", {}));
    return;
  }
  if (action === "next" || action === "scenario") {
    const scenario =
      id || state.data.scenarios[Math.min(state.data.replay_step + 1, 3)].id;
    await act(() => api("/replay", { scenario_id: scenario }));
    return;
  }
  if (action === "fault") {
    await act(() => api("/replay/fault", { fault: id }));
    return;
  }
  if (["collect", "pause", "resume"].includes(action)) {
    if (state.mode !== "live") {
      toast("回放不能操作正式来源");
      return;
    }
    await act(() => api(`/sources/${id}/${action}`, { mode: state.mode }));
  }
});
$("close-dialog").addEventListener("click", () => $("detail-dialog").close());
$("detail-dialog").addEventListener("click", (e) => {
  if (e.target === $("detail-dialog")) $("detail-dialog").close();
});
$("refresh-btn").addEventListener("click", refresh);
$("clock").textContent = new Date().toLocaleDateString("zh-CN", {
  timeZone: "Asia/Shanghai",
  year: "numeric",
  month: "long",
  day: "numeric",
  weekday: "long",
});
refresh();
setInterval(() => {
  if (!state.busy && !$("detail-dialog").open) refresh();
}, 15000);
