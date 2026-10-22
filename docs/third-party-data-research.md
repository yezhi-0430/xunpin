# 第三方商品数据接入核查

核查日期：2026-10-21。范围为公开产品文档和服务条款；未注册第三方账号、购买套餐、发送咨询或执行商品数据 API 请求。当前没有确认满足题目来源要求的第三方服务。

## 候选比较

| 候选 | 已查到的公开能力 | 接入条件与缺口 | 当前判断 |
| --- | --- | --- | --- |
| Just One API | 商品详情、SKU、描述 HTML，另有商品评价接口 | 文档请求需要服务 Token 和商品 ID；指南说明注册后有免费测试次数，具体价格与额度需登录确认；未核实上游许可及本项目用途 | 技术候选，不能标记为已获准来源 |
| 折淘客 | 标题、简介、价格、券金额与条件、更新时间等；支持商品库增量查询 | 商品详情和全站商品库文档均要求 appkey、淘客授权 ID（sid）与同账号 PID；详情参数说明支持淘宝客商品；评价数量不等于评价正文 | 当前仍有淘客接入条件，不能承诺个人无阵地可直接使用 |
| 好单库 | 自有产品介绍列出导购商品库、详情、搜索和推荐能力 | 本次未完整核实商品详情接口的准入、字段与价格；导购用途不等于竞品监控获准 | 备选，暂不推荐注册或付费 |
| 万邦 / OneBound | 文档提供商品数据 API；官方 FAQ 描述 PC 端采集 | 未找到本次用途所需的平台许可证明；其另一套买卖家接口要求同步 Cookie，不应混同为商品 API 的全部接入要求 | 不因有接口而认定为授权来源 |

来源：

- [Just One API 商品详情 V9](https://docs.justoneapi.com/zh/api/taobao-and-tmall/product-details-v9)：GET `/api/taobao/get-item-detail/v9`，查询参数 `token` 和 `itemId`。不能由原价或 SKU 价格直接推导所有用户到手价。
- [Just One API 商品评价 V3](https://docs.justoneapi.com/zh/api/taobao-and-tmall/product-reviews-v3)：独立评价接口，实际样本范围、分页和稳定 ID 尚待真实响应核对。
- [Just One API 使用指南](https://docs.justoneapi.com/zh/usage)：试用与定价以实际账号为准；未核实可用次数或单价。
- [Just One API 服务条款](https://justoneapi.com/zh/terms)：说明 API 依赖第三方平台和实时采集链路，使用者需具备收集、存储、分析及继续使用数据的依据。该条款本身不构成淘宝授权证明。
- [折淘客商品详情](https://www.zhetaoke.com/user/extend/extend_lingquan_detail.aspx) 与 [全站商品库](https://www.zhetaoke.com/user/extend/extend_lingquan_site.aspx)：两者均公告 sid、pid 为必填，不依据旧示例里省略这两个参数的 URL 判断可匿名接入。
- [好单库解决方案](https://www.haodanku.com/openapi/api_plan) 与 [接口服务协议](https://www.haodanku.com/openapi/api_protocol)。
- [万邦 API 常见问题](https://console.open.onebound.cn/console/?article_id=4&do=detail&go=article&query=go%3Darticle%26do%3Dbrowse%26page%3D1) 与 [其买卖家接口说明](https://open.onebound.cn/oneapi/)。后者的 Cookie 同步流程不作为本项目接入方案。

## 接入现有项目需要做什么

现有 provider 是本项目自定义的 `GET /products/{id}` + Bearer Token 契约，不是上述厂商的现成适配器。仅填写服务域名和 Token 无法直接接通。

选择供应商并确认用途后，需按真实协议增加适配：固定 HTTPS 域名和路径；处理服务业务码与预算；将实际字段映射为 Snapshot；保留来源时间、SKU 与优惠条件；未返回的描述或评价保持缺失。Just One API 使用查询参数 Token，异常与日志不得泄露完整请求 URL。它的文档建议较长超时，现有 20 秒超时也需要依据真实测试重新评估。

首轮只验证一个获准商品，不启用全网搜索或批量采集。核对实际响应、字段语义和新鲜度，再接入已有的调度、去重与暂停逻辑。未获准外发时保持 AI 分析关闭。

## 给候选服务商的咨询草稿（未发送）

个人开发者准备制作非商业面试 demo，低频监控少量指定淘宝/天猫商品的标题、价格、促销与公开评价，用于竞品变化分析。无可授权店铺，也无实际淘客推广阵地。

请确认：

1. 这种身份和用途能否申请，是否另需淘宝联盟 PID 或商家授权？
2. 上游数据通过何种获准方式取得，可否提供适用的授权或平台规则依据？
3. 是否允许定时监控、保存历史快照，以及将必要字段交给模型分析？
4. 是否允许在 GitHub 项目截图和面试录屏中展示少量返回数据，哪些内容必须隐藏？
5. 试用覆盖哪些接口、额度、缓存时效与速率限制，失败和空结果是否计费？

下一步优先核实 Just One API 的准入与来源许可；这只是待核验的技术候选，不是已选定或已接通的授权来源。
