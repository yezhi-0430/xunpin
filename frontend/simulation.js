"use strict";
let simulationOffset = 0;

function simulationEmpty() {
  return panel("数据尚未导入", "等待本地数据文件", empty("等待本地模拟数据", "通过本地导入工具读取 Claude 的模拟数据库后，这里展示商品、告警和建议。"));
}

function simulationOverview() {
  const d = state.data;
  if (!d.manifest) return simulationEmpty();
  const platforms = {};
  for (const p of d.products) platforms[p.platform] = (platforms[p.platform] || 0) + 1;
  const distribution = `<div class="panel-body">${Object.entries(platforms).map(([name, total]) => `<div class="mini-kv"><span>${esc(name)}</span><strong>${total} 件模拟商品</strong></div>`).join("")}<hr class="divider"><p class="subtle">不同品类与平台的构造样例，不直接作为同规格竞品比价。价格为模拟标价，未提供券门槛与到手价条件。</p></div>`;
  return `<div class="metrics">${metric("监控商品", d.products.length, "件", "已导入的本地商品记录")}${metric("告警记录", d.events.length, "条", `${d.events.filter(e => e.priority === "high").length} 条标为高优先级`, true)}${metric("待处理建议", d.tasks.filter(t => t.status === "pending").length, "项", "待人工核实与处理")}${metric("分析记录", d.analyses.length, "条", "来自已保存的分析数据")}</div><div class="grid-two">${panel("最近告警", "来自导入数据，点击查看详情", simulationAlertRows(d.events.slice(0, 4)), '<button class="text-button" data-page="events">全部告警 →</button>')}${panel("商品分布", "按导入样例统计", distribution, tag("构造样例", "gray"))}</div>${panel("监控商品", "已导入商品记录，标价与到手价分开说明", simulationProducts())}`;
}

function simulationAlertRows(events) {
  return events.map(e => `<div class="event-row"><div class="event-symbol ${e.kind === "price" ? "price" : "availability"}">${e.kind === "price" ? "↘" : "!"}</div><div class="event-main"><h3>${esc(e.title)}</h3><p>${esc(e.product_title)}</p><div class="event-fact">来自已导入的告警记录</div></div><div class="event-meta">${tag(e.priority === "high" ? "高优先级" : "待核实", e.priority === "high" ? "red" : "gray")}<button class="text-button" data-action="simulation-detail" data-id="${esc(e.id)}">查看详情 ↗</button></div></div>`).join("") || empty("没有告警记录", "本地导入后展示已有记录，不自动编造变化。");
}

function simulationProducts() {
  const rows = state.data.products;
  simulationOffset = Math.max(0, Math.min(simulationOffset, Math.max(0, Math.ceil(rows.length / 20) - 1) * 20));
  const selected = rows.slice(simulationOffset, simulationOffset + 20);
  return `<div class="page-toolbar" style="padding:16px 22px"><span class="subtle">第 ${rows.length ? simulationOffset + 1 : 0}–${simulationOffset + selected.length} 条 / 共 ${rows.length} 件模拟商品</span><div class="actions"><button class="secondary" data-action="simulation-page" data-offset="${simulationOffset - 20}" ${simulationOffset === 0 ? "disabled" : ""}>上一页</button><button class="secondary" data-action="simulation-page" data-offset="${simulationOffset + 20}" ${simulationOffset + 20 >= rows.length ? "disabled" : ""}>下一页</button></div></div><div class="table-wrap"><table><thead><tr><th>商品样例</th><th>平台 / 品类</th><th>模拟标价</th><th>模拟销量 / 库存</th><th>查看</th></tr></thead><tbody>${selected.map(p => `<tr><td><strong>${esc(p.title)}</strong><small>样例编号 ${esc(p.product_id)}</small></td><td>${esc(p.platform)}<small>${esc(p.category || "未提供")}</small></td><td>${money(p.price_cents)}<small>到手价未提供</small></td><td>${esc(p.sales ?? "未提供")} / ${esc(p.stock ?? "未提供")}</td><td><button class="text-button" data-action="simulation-detail" data-id="product:${esc(p.product_id)}">详情 ↗</button></td></tr>`).join("")}</tbody></table></div>`;
}

function simulationEvents() {
  if (!state.data.manifest) return simulationEmpty();
  return panel("告警记录", "已导入的告警条目；查看商品与原始说明", simulationAlertRows(state.data.events));
}

function simulationTasks() {
  if (!state.data.manifest) return simulationEmpty();
  return `<p class="subtle">以下为导入的模拟建议，需人工核实商品、成本和库存后再决定。原样例中的置信度与销量、利润预测没有实测依据，不作为有效结论展示。</p><div class="task-grid">${state.data.tasks.map(t => `<article class="task"><div class="task-top">${tag(t.status === "pending" ? "待处理" : "原数据已标记", "amber")}${tag("构造建议", "gray")}</div><h3>${esc(t.title)}</h3><p>${esc(t.action)}</p><p class="subtle">${esc(t.product_title)}<br>样例编号：${esc(t.product_id)}<br>此状态不代表已在平台执行。</p><div class="task-metric">执行前：核实商品属性、价格条件与成本。<br>评估：取得真实观察数据后制定指标，不承诺收益。</div></article>`).join("")}</div>`;
}

function simulationSources() {
  const m = state.data.manifest;
  if (!m) return simulationEmpty();
  return panel("数据来源", "本地导入，不发起淘宝或模型请求", `<div class="panel-body"><p><strong>${esc(m.source)}</strong> · ${esc(m.source_file)}</p><p>导入时间：${date(m.imported_at)}<br>${esc(m.limitation)}</p><p class="subtle history-provenance">原文件 SHA-256：${esc(m.source_sha256)}</p><p>在线接口尚未接通；真实天池文案从侧栏“真实历史样本”查看。</p></div>`);
}

function showSimulationDetail(id) {
  const d = state.data;
  const event = d.events.find(e => e.id === id);
  const productId = event ? event.product_id : id.replace(/^product:/, "");
  const p = d.products.find(p => p.product_id === productId);
  if (!p) { toast("未找到该模拟商品"); return; }
  const history = d.analyses.find(a => a.product_id === productId && a.kind === "price_trend")?.result?.price_history;
  $("dialog-kicker").textContent = "模拟商品 / 原始构造记录";
  $("dialog-body").innerHTML = `<h2>${esc(p.title)}</h2><div class="evidence-facts">构造样例，非真实淘宝观测。此导入没有可核实的前后快照，不作为真实变化证据；不会触发旧版随机分析或模型请求。</div>${event ? `<h3>${esc(event.title)}</h3><p>${esc(event.message)}</p>` : ""}<p>样例编号：${esc(p.product_id)}<br>模拟标价：${money(p.price_cents)} · 到手价：未提供<br>平台 / 品类：${esc(p.platform)} / ${esc(p.category || "未提供")}</p>${Array.isArray(history) ? `<h3>已导入的模拟价格历史</h3><p class="subtle">来自原数据库的构造序列，非真实价格走势；不重新生成随机数据。</p><div class="table-wrap"><table><thead><tr><th>样例日期</th><th>模拟价格</th></tr></thead><tbody>${history.map(r => `<tr><td>${esc(r.date)}</td><td>${Number.isFinite(Number(r.price)) ? money(Math.round(Number(r.price) * 100)) : "未提供"}</td></tr>`).join("")}</tbody></table></div>` : '<p class="subtle">该商品未导入价格历史，保持未知。</p>'}`;
  $("detail-dialog").showModal();
}
