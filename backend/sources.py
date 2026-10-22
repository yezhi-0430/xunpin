import hashlib
import json
from datetime import datetime, timezone, timedelta
from decimal import Decimal, InvalidOperation
from email.utils import parsedate_to_datetime
from urllib.parse import quote, urlparse

import httpx
from pydantic import ValidationError

from .domain import Snapshot, clean, now_iso


class SourceError(Exception):
    def __init__(self, kind, message, retry_after=0):
        super().__init__(message)
        self.kind, self.retry_after = kind, retry_after


def top_sign(params, secret):
    packed = "".join(k + str(params[k]) for k in sorted(params) if k != "sign")
    return hashlib.md5((secret + packed + secret).encode("utf-8")).hexdigest().upper()


def secure_url(url):
    parsed = urlparse(url)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise SourceError(
            "schema", "source endpoint must be a credential-free HTTPS base URL"
        )
    return url.rstrip("/")


def retry_delay(value):
    try:
        return max(1, min(86400, int(value)))
    except (ValueError, TypeError):
        try:
            return max(
                1,
                min(
                    86400,
                    int(
                        (
                            parsedate_to_datetime(value) - datetime.now(timezone.utc)
                        ).total_seconds()
                    ),
                ),
            )
        except (ValueError, TypeError, OverflowError):
            return 0


def decode_response(response):
    if response.status_code == 429:
        raise SourceError(
            "rate",
            "来源返回限流，等待后再尝试",
            retry_delay(response.headers.get("Retry-After")),
        )
    if response.status_code in [401, 403]:
        raise SourceError("auth", "授权失效或权限不足，需要人工恢复")
    if (
        300 <= response.status_code < 400
        or "text/html" in response.headers.get("content-type", "").lower()
    ):
        raise SourceError("challenge", "来源返回跳转或 HTML 页面，已暂停核查")
    if response.status_code >= 500:
        raise SourceError("transient", "来源服务暂时不可用")
    if response.status_code >= 400 or len(response.content) > 2_000_000:
        raise SourceError("schema", "来源响应状态或体积异常")
    try:
        data = response.json()
    except (ValueError, UnicodeDecodeError):
        raise SourceError("schema", "来源未返回有效 JSON") from None
    if not isinstance(data, dict):
        raise SourceError("schema", "来源响应结构异常")
    if "error_response" in data:
        error = data["error_response"]
        if not isinstance(error, dict):
            raise SourceError("schema", "业务错误结构异常")
        code = str(error.get("code", ""))
        hint = (
            str(error.get("sub_code", "")) + " " + str(error.get("msg", ""))
        ).lower()
        if any(k in hint for k in ["limit", "frequency", "限流"]):
            raise SourceError("rate", "来源返回业务限流")
        kind = (
            "auth"
            if code == "27"
            or any(k in hint for k in ["session", "permission", "auth", "权限"])
            else "schema"
        )
        raise SourceError(kind, "来源返回业务错误，需核对权限与接口契约")
    return data


def fetch_snapshot(source, product_id, transport=None):
    if not source.ready() or product_id not in source.product_ids:
        raise SourceError("auth", "来源未获配置许可或商品超出范围")
    try:
        with httpx.Client(
            timeout=20, follow_redirects=False, transport=transport, trust_env=False
        ) as client:
            if source.kind == "official":
                if not product_id.isdigit():
                    raise SourceError("schema", "官方商品 ID 必须为数字")
                params = dict(
                    method="taobao.item.seller.get",
                    app_key=source.app_key,
                    session=source.session,
                    timestamp=datetime.now(timezone(timedelta(hours=8))).strftime(
                        "%Y-%m-%d %H:%M:%S"
                    ),
                    format="json",
                    v="2.0",
                    sign_method="md5",
                    fields="num_iid,title,price,desc",
                    num_iid=product_id,
                )
                params["sign"] = top_sign(params, source.app_secret)
                data = decode_response(
                    client.post("https://eco.taobao.com/router/rest", data=params)
                )
                item = data.get("item_seller_get_response", {}).get("item")
                if not isinstance(item, dict) or str(item.get("num_iid")) != product_id:
                    raise SourceError(
                        "schema", "official response identity mismatch or missing item"
                    )
                price = (
                    int((Decimal(str(item["price"])) * 100).quantize(Decimal("1")))
                    if item.get("price") is not None
                    else None
                )
                return Snapshot(
                    product_id=product_id,
                    title=item.get("title", ""),
                    role="own",
                    source_id=source.source_id,
                    observed_at=now_iso(),
                    description=clean(item["desc"])[:16000]
                    if item.get("desc") is not None
                    else None,
                    price_cents=price,
                    coupon_cents=None,
                    price_confirmed=False,
                    reviews=None,
                )
            base = secure_url(source.base_url)
            data = decode_response(
                client.get(
                    f"{base}/products/{quote(product_id, safe='')}",
                    headers={
                        "Authorization": f"Bearer {source.token}",
                        "Accept": "application/json",
                    },
                )
            )
            if str(data.get("product_id")) != product_id:
                raise SourceError("schema", "provider response identity mismatch")
            data.update(source_id=source.source_id, role="competitor")
            return Snapshot(**data)
    except (httpx.TimeoutException, httpx.NetworkError):
        raise SourceError("transient", "来源超时或网络故障") from None
    except (ValidationError, InvalidOperation, KeyError, TypeError, AttributeError):
        raise SourceError("schema", "响应字段不符合来源契约，已隔离") from None
