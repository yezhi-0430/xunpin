from copy import deepcopy

from .domain import Snapshot, now_iso

SCENARIOS = [
    {
        "id": "baseline",
        "title": "建立监控基线",
        "detail": "本店与三件同规格竞品；首次观测不推断历史变化。",
    },
    {
        "id": "price-drop",
        "title": "竞品实际降价",
        "detail": "竞品到手价从 69 元降至 59 元；计算价差并建立促销测算任务。",
    },
    {
        "id": "coupon-offset",
        "title": "标价与优惠抵消",
        "detail": "标价 99→89 元、优惠券 30→20 元；到手价仍为 69 元，不误报降价。",
    },
    {
        "id": "content-change",
        "title": "卖点与评价变化",
        "detail": "新增旅行场景描述与评价样本，生成信息核查和美工需求草稿。",
    },
]


def products_for(step):
    rows = []
    review_time = "2026-10-21T08:00:00+08:00"
    for i, (pid, title) in enumerate(
        [
            ("1001", "本店 · 澄川轻净洗发水"),
            ("2001", "竞品 A · 清衡洗发水"),
            ("2002", "竞品 B · 植序洗发水"),
            ("2003", "竞品 C · 沐简洗发水"),
        ]
    ):
        rows.append(
            dict(
                product_id=pid,
                title=title + " 500ml",
                role="own" if i == 0 else "competitor",
                source_id="replay",
                observed_at=now_iso(),
                description="500ml 单瓶装，使用方法与容量标识。",
                spec="500ml×1",
                conditions="单件 / 公开券 / 上海 / 非会员",
                price_cents=9900 if i else 8900,
                coupon_cents=3000 if i else 2000,
                coupon_threshold_cents=0,
                price_confirmed=True,
                promotion_text="公开券，无额外叠加",
                reviews=[
                    dict(
                        review_id=f"{pid}-r0",
                        text="容量标识清楚",
                        observed_at=review_time,
                    )
                ],
            )
        )
    if step >= 1:
        rows[1]["price_cents"] = 8900
    if step >= 2:
        rows[2].update(price_cents=8900, coupon_cents=2000)
    if step >= 3:
        rows[3].update(
            title="竞品 C · 沐简旅行场景洗发水 500ml",
            description="500ml 单瓶装；新增旅行分装与收纳场景说明。",
        )
        rows[3]["reviews"].append(
            dict(
                review_id="2003-r1",
                text="希望有旅行分装说明，携带时担心漏液。",
                observed_at=review_time,
            )
        )
        rows[0]["reviews"].append(
            dict(
                review_id="1001-r1",
                text="能否加一张分装与收纳示意图？",
                observed_at=review_time,
            )
        )
    return [Snapshot(**deepcopy(r)) for r in rows]
