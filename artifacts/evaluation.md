# 变化识别离线评估

数据集版本：2026-10-21-v2；SHA-256：`7c36535e0213f5c574c9cca5b66fd98f0bf5806d8a3390692397f12a06e32fad`。

固定构造快照的规则变化识别；不是独立人工标注集、真实市场样本或模型质量评估。期望标签按业务含义预先编写。

评估对象是 `backend.domain.changes`，未调用商品 API 或大模型。标签与程序由同一项目编写，存在覆盖偏差；高分不能证明真实数据泛化能力。持久化去重、来源状态机和模型语义质量不由本报告评分，另见自动测试和后续接入验收。

案例完全匹配：**62 / 62**（100.0%）；事实断言失败案例：0。

事件标签微平均：精确率 100.0%，召回率 100.0%。

| 类型 | TP | FP | FN | 精确率 | 召回率 |
| --- | ---: | ---: | ---: | ---: | ---: |
| 可比价格 | 13 | 0 | 0 | 100.0% | 100.0% |
| 促销与条件 | 10 | 0 | 0 | 100.0% | 100.0% |
| 标题 | 6 | 0 | 0 | 100.0% | 100.0% |
| 描述 | 8 | 0 | 0 | 100.0% | 100.0% |
| 评价样本 | 6 | 0 | 0 | 100.0% | 100.0% |
| 字段覆盖 | 9 | 0 | 0 | 100.0% | 100.0% |

TP 为正确事件标签，FP 为多报，FN 为漏报。同一案例可能包含多个事件；金额等事实错误会让案例失败，即使事件标签正确。无变化案例通过完全匹配率和分组统计体现，避免仅报事件精确率遗漏正常样本。

## 分组覆盖

| 分组 | 通过 | 总数 |
| --- | ---: | ---: |
| 无变化 | 8 | 8 |
| 可比价格 | 12 | 12 |
| 促销与条件 | 10 | 10 |
| 文本变化 | 10 | 10 |
| 评价样本 | 6 | 6 |
| 字段覆盖 | 16 | 16 |

## 逐案例结果

| 案例 | 说明 | 期望事件 | 实际事件 | 结果 |
| --- | --- | --- | --- | --- |
| stable-01 | 完全相同快照 | 无 | 无 | 通过 |
| stable-02 | 仅观测时间更新 | 无 | 无 | 通过 |
| stable-03 | 标题多余空格不构成内容变化 | 无 | 无 | 通过 |
| stable-04 | 描述包裹HTML标签但正文相同 | 无 | 无 | 通过 |
| stable-05 | 评价顺序调整不构成新增 | 无 | 无 | 通过 |
| stable-06 | 已见评价ID再次返回不是新增样本；评价编辑检测不在当前范围 | 无 | 无 | 通过 |
| stable-07 | 滚动窗口为空不推断差评被删除 | 无 | 无 | 通过 |
| stable-08 | 始终缺失的字段不重复报警 | 无 | 无 | 通过 |
| price-01 | 同条件降价10元 | price | price | 通过 |
| price-02 | 同条件涨价10元 | price | price | 通过 |
| price-03 | 一分钱变化仍精确保存 | price | price | 通过 |
| price-04 | 大幅涨价不产生重复促销事件 | price | price | 通过 |
| price-05 | 降价后不满券门槛仍需准确计算 | price | price | 通过 |
| price-06 | 标价上涨满足门槛后到手价反而下降 | price | price | 通过 |
| price-07 | 券额增加导致可比降价 | price | price | 通过 |
| price-08 | 券额减少导致可比涨价 | price | price | 通过 |
| price-09 | 明确零价格不是字段缺失 | price | price | 通过 |
| price-10 | 来源明确确认的零价格保留 | price | price | 通过 |
| price-11 | 门槛由不满足变为满足 | price | price | 通过 |
| price-12 | 门槛由满足变为不满足 | price | price | 通过 |
| promo-01 | 标价与券额抵消 | promotion | promotion | 通过 |
| promo-02 | 赠品说明改变但当前可比价格未变 | promotion | promotion | 通过 |
| promo-03 | 会员条件变化不能报可比降价 | promotion | promotion | 通过 |
| promo-04 | 券门槛调整但都未达门槛 | promotion | promotion | 通过 |
| promo-05 | 价格确认标记撤回需核查机制 | promotion | promotion | 通过 |
| promo-06 | 地区不同不能比较报价 | promotion | promotion | 通过 |
| promo-07 | 两件价不能和单件价比较 | promotion | promotion | 通过 |
| promo-08 | 规格变化并伴随价格变化，不能报可比降价 | promotion | promotion | 通过 |
| promo-09 | 未知规格下只记录报价核查 | promotion | promotion | 通过 |
| promo-10 | 未满足门槛时券额变化不是降价 | promotion | promotion | 通过 |
| text-01 | 标题新增场景 | title | title | 通过 |
| text-02 | 描述新增容量说明 | description | description | 通过 |
| text-03 | 标题与描述同时变化 | title、description | title、description | 通过 |
| text-04 | 标题功效变化只记录文本不证明功效 | title | title | 通过 |
| text-05 | 描述删除为明确空串有别于未知 | description | description | 通过 |
| text-06 | 空描述增加正文 | description | description | 通过 |
| text-07 | 标题中数字变化必须保留证据 | title | title | 通过 |
| text-08 | HTML正文真实变化 | description | description | 通过 |
| text-09 | 新增未经验证承诺仍只记录事实 | description | description | 通过 |
| text-10 | 多字段同时变化不能漏掉价格 | title、description、price | title、description、price | 通过 |
| reviews-01 | 新增一条评价只报告一个样本 | reviews | reviews | 通过 |
| reviews-02 | 新增重复ID只算一条 | reviews | reviews | 通过 |
| reviews-03 | 旧新混合只统计新增ID | reviews | reviews | 通过 |
| reviews-04 | 相同文字不同ID仍是两个样本，不自行判断刷评 | reviews | reviews | 通过 |
| reviews-05 | 包含指令的评价仅作为样本，不能执行 | reviews | reviews | 通过 |
| reviews-06 | 本店评价与标题变化并行记录 | title、reviews | title、reviews | 通过 |
| coverage-01 | 标价缺失不能称为促销调整 | availability | availability | 通过 |
| coverage-02 | 券额缺失不能当作撤券 | availability | availability | 通过 |
| coverage-03 | 描述未知不能当作正文删除 | availability | availability | 通过 |
| coverage-04 | 评价缺失不等于没有评价 | availability | availability | 通过 |
| coverage-05 | 多个字段同时缺失只生成覆盖事件 | availability | availability | 通过 |
| coverage-06 | 首次恢复标价建立字段基线，不推断降价或促销变化 | 无 | 无 | 通过 |
| coverage-07 | 首次恢复券额不推断撤券 | 无 | 无 | 通过 |
| coverage-08 | 首次恢复描述建立字段基线 | 无 | 无 | 通过 |
| coverage-09 | 首次取得评价窗口不冒充新增评价 | 无 | 无 | 通过 |
| coverage-10 | 促销文案暂不可用不能认定活动调整 | availability | availability | 通过 |
| coverage-11 | 首次取得促销文案不推断活动发生变化 | 无 | 无 | 通过 |
| coverage-12 | 缺价格但描述真实变化仍可识别 | description、availability | description、availability | 通过 |
| coverage-13 | 券门槛未知不能默认成零门槛 | availability | availability | 通过 |
| coverage-14 | 券门槛恢复先建立字段基线 | 无 | 无 | 通过 |
| coverage-15 | 确认标志未知不能默认成已确认 | availability | availability | 通过 |
| coverage-16 | 首次取得确认标志不推断降价 | 无 | 无 | 通过 |

## 重跑

```powershell
uv run python -m evaluations.run --check --json artifacts/evaluation.json --markdown artifacts/evaluation.md
```

`--check` 在案例或事实不匹配时返回非零退出码。评估只读本地案例，不读取 .env、不修改工作台数据库，也不请求网络。
