# 授权数据服务契约

这是一层由本项目定义的适配契约，**不是任何现有服务商的接口文档，也不是平台已授权证明**。选择服务后，根据其真实文档转换至该结构，完成许可与小样本验证。没有选定服务时保持停用。

## 许可与配置

`PROVIDER_PERMITTED=true` 表示本地配置人已确认用途与范围，必须同时提供 `PROVIDER_PERMISSION_REF`、凭证和商品白名单。软件不能自动验证合同真实性；许可核验记录应存放在私有位置。`PROVIDER_EXPIRES_AT` 可配置带时区的到期时间；未填写时由配置人管理撤销期限。

仅使用本地固定配置的 HTTPS 地址，不接受来自模型、商品内容或前端输入的目标网址，不跟随重定向。提供方也必须符合数据获取规则；仅有 HTTP 接口不等于获准使用。

## 请求

```http
GET {PROVIDER_BASE_URL}/products/{product_id}
Authorization: Bearer <本地令牌>
Accept: application/json
```

商品 ID 必须在本地白名单内，响应身份必须一致。一个来源每次调度只发一个请求，按白名单轮转；首轮未完成时按最小间隔继续，完成一轮后等待配置周期。每日预算按北京时间计算。

## 标准化响应示例

下面是结构示例，数值与文字均为构造内容。服务端需将其真实字段转换为这一结构；没有返回的可选字段应为 `null` 或省略，不能填造默认事实。

```json
{
  "product_id": "2001",
  "source_id": "provider",
  "role": "competitor",
  "title": "示例洗发水 500ml",
  "description": "示例描述",
  "observed_at": "2026-10-21T10:00:00+08:00",
  "spec": "500ml×1",
  "conditions": "单件 / 公开券 / 上海 / 非会员",
  "price_cents": 9900,
  "coupon_cents": 2000,
  "coupon_threshold_cents": 0,
  "price_confirmed": true,
  "promotion_text": "公开券，无其他叠加",
  "reviews": [
    {"review_id": "sample-r1", "text": "示例评价", "observed_at": "2026-10-21T08:00:00+08:00"}
  ]
}
```

- `observed_at` 为来源观测时间，必须带时区；不能把陈旧缓存改写成当前观测。
- 金额单位是分。`coupon_cents=0` 仅用于明确无优惠的情况；未知使用 `null`。
- `price_confirmed=true` 表示本契约涉及的购买条件与优惠计算已由适配器确认，不代表所有用户享有相同价格。若叠加、有效期或身份不确定，设为 false。
- `price_confirmed` 省略时为未知，不默认确认；`coupon_threshold_cents` 省略时也为未知，不默认零门槛。有优惠券但门槛未知时不计算到手价。无优惠券须明确返回 `coupon_cents=0`，此时券门槛不参与计算。
- 已知字段变为未知只触发覆盖提示；恢复后先建立字段基线，不把恢复过程认作降价或促销调整。
- 当前实现仅处理一种已经确认有效的单件优惠；复杂满减、赠品、多券叠加不自行推断，应在上游适配后明确规则或保持价格未知。
- `reviews=null` 表示未提供；空列表表示该窗口无样本。每个来源与商品按评价 ID 持久去重，允许滚动窗口重复返回。当前实现不包含通用评价分页器，需由所选来源适配处理增量和分页，不宣称已覆盖全量评价。
- 描述、价格或评价缺失会保留覆盖状态；不把缺失解释为下架。
- 超过 `MAX_AGE_SECONDS` 的响应不覆盖当前快照，乱序观测也不会倒退最新状态。缓存变旧后退出当前报价对照，历史快照仍可追溯。

## 当前官方适配器范围

官方路径实现 `taobao.item.seller.get` 的签名请求，仅用于已配置白名单中的自店商品，映射商品身份、标题、标价及实际返回的描述。它**没有实现**促销接口、SKU 解析或评价分页；因此不计算活动到手价，也不宣称单靠该接口覆盖题目全部字段。应在取得实际权限与样本后继续扩展。

接口名称、签名和权限参照 [淘宝商品接口](https://developer.alibaba.com/docs/api.htm?apiId=24625&source=search) 与 [TOP 请求协议](https://developer.alibaba.com/docs/doc.htm?articleId=101617&docType=1&treeId=49)。接口仍需在实际控制台核实，契约测试不代表已取得权限。

## AI 接入

兼容 Chat Completions 的模型网关由本地配置，不内置密钥。`.env.example` 提供用户选定的 DeepSeek 预设：`LLM_BASE_URL=https://api.deepseek.com`、`LLM_MODEL=deepseek-flash`；模型别名映射及请求参数见 [DeepSeek 官方文档](https://api-docs.deepseek.com/)、[Chat Completions](https://api-docs.deepseek.com/api/create-chat-completion/)。调用最多两次 HTTP：模型可选一次只读证据或缓存工具，再返回结构化建议。每次请求最多输出 4096 tokens；官方 DeepSeek 请求显式关闭 thinking，不传未在该文档说明的 parallel_tool_calls 参数。

真实数据须分别确认来源允许外发分析，且来源许可和商品范围仍有效；默认关闭。每个 HTTP 模型请求计入独立每日预算。失败、格式或证据校验不通过则显示规则结果。

输出契约版本 2 包含 summary、evidence_ids、findings、hypotheses、recommendations、limitations；建议须带 evaluation_plan。每个语义发现引用 `ref + quote`，程序检查逐字片段确实出现在本次发给模型的字段节选里，并补回快照版本和评价 ID。模型可以返回空发现。评价引用仅限本事件新增样本，引用数由程序按评价 ID 去重计算。

输入正文每字段每版本最多 3000 字符；评价最多 20 条、每条 600 字符，证据包约束在 29000 JSON 字符内并标注截断。工具不会补回未发送的完整正文。金额依据明确的整数分字段校验，促销前后金额统一换算；标识符数字不作为数量依据，显式评价样本数量核对程序计数。上述检查不证明观察与引用的语义蕴含、因果、主题完整性或绝无幻觉，运营仍需审核。

任务反馈可选 `execution`：计划日期、观察起止日期、实际动作、指标及单位、基线/结果值、数据来源、干扰因素和评估状态。observed 须有完整观察证据，inconclusive 须说明原因；已评估任务新提交的复盘不能仍为 pending。省略 execution 的旧客户端不清除既有记录。每次历史反馈保存当次复盘快照；这些记录由人填写，不表示已在淘宝执行动作或证明因果收益。
