"""
支付宝沙箱集成 — 真实签名请求 + 异步通知验签（不是 mock）
================================================================
这是"没绑定支付"这件事的解法：接支付宝开放平台的官方沙箱环境。协议、签名算法、
回调验签流程和生产环境完全一样，唯一区别是钱不是真的——不需要企业/个体工商户资质，
个人开发者账号就能开沙箱应用。

拿沙箱凭证的步骤（你自己做，我这边做不了，因为要登录你的账号）：
  1. 打开 https://open.alipay.com → 用支付宝账号登录 → 控制台 → 沙箱
  2. 沙箱应用会给你：APPID、支付宝公钥（Alipay Public Key）
  3. 你自己生成一对 RSA2 密钥（沙箱页面有"密钥生成工具"链接，或者本地用
     openssl genrsa -out app_private_key.pem 2048 生成），把生成的应用公钥
     贴回沙箱页面的"设置应用公钥"，拿到应用私钥
  4. 把这三样配进环境变量（不要提交进 git，写进 .env / 部署平台的密钥管理）：
       ALIPAY_APP_ID=你的沙箱 APPID
       ALIPAY_PRIVATE_KEY=你的应用私钥（去掉 -----BEGIN/END----- 和换行）
       ALIPAY_PUBLIC_KEY=沙箱页面给的"支付宝公钥"（同样去掉头尾和换行）
  5. 本地测试异步通知（notify_url）需要公网可达，用你项目里已经在用的 ngrok
     开一个隧道指到 8765，把 ALIPAY_NOTIFY_URL 设成
     https://<你的ngrok域名>/api/boss/recharge/alipay/notify
  6. 沙箱买家账号 + 沙箱买家 App（支付宝开放平台同一个沙箱页面能下载）用来扫码/登录付款

没配这三个环境变量之前，下面的函数会自动降级成标注清楚的 mock（不会被误当成真实集成）。
依赖 pycryptodome（已加进 requirements.txt），import 延迟到真正要签名/验签时才做，
所以没装这个包也不影响其它功能正常跑。
"""

import os
import json
import time
from urllib.parse import quote_plus

ALIPAY_APP_ID = os.getenv("ALIPAY_APP_ID", "")
ALIPAY_PRIVATE_KEY = os.getenv("ALIPAY_PRIVATE_KEY", "")
ALIPAY_PUBLIC_KEY = os.getenv("ALIPAY_PUBLIC_KEY", "")
ALIPAY_NOTIFY_URL = os.getenv("ALIPAY_NOTIFY_URL", "")
ALIPAY_GATEWAY = "https://openapi-sandbox.dl.alipaydev.com/gateway.do"


def alipay_configured() -> bool:
    """三个凭证都配了才算真的接了沙箱；否则调用方应该降级成 mock。"""
    return bool(ALIPAY_APP_ID and ALIPAY_PRIVATE_KEY and ALIPAY_PUBLIC_KEY)


def _rsa_sign(unsigned: str) -> str:
    from Crypto.PublicKey import RSA
    from Crypto.Signature import PKCS1_v1_5
    from Crypto.Hash import SHA256
    import base64

    pem = "-----BEGIN RSA PRIVATE KEY-----\n" + ALIPAY_PRIVATE_KEY.strip() + "\n-----END RSA PRIVATE KEY-----"
    key = RSA.import_key(pem)
    h = SHA256.new(unsigned.encode("utf-8"))
    signer = PKCS1_v1_5.new(key)
    return base64.b64encode(signer.sign(h)).decode("utf-8")


def build_page_pay_url(out_trade_no: str, amount_cny: float, subject: str) -> str:
    """生成支付宝网页支付跳转链接（alipay.trade.page.pay，沙箱网关）。

    配好三个环境变量后，这个函数返回的是一条真实可跳转、真实签名过的支付宝沙箱支付链接——
    可以在沙箱买家 App 里扫码/登录付款，不是随便拼一个字符串。没配则退化成
    mockpay://alipay?... 占位符，前端应该按 is_real_gateway 标注清楚给用户看。
    """
    if not alipay_configured():
        return f"mockpay://alipay?order={out_trade_no}&amount={amount_cny}"

    biz_content = {
        "out_trade_no": out_trade_no,
        "product_code": "FAST_INSTANT_TRADE_PAY",
        "total_amount": f"{amount_cny:.2f}",
        "subject": subject,
    }
    params = {
        "app_id": ALIPAY_APP_ID,
        "method": "alipay.trade.page.pay",
        "charset": "utf-8",
        "sign_type": "RSA2",
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "version": "1.0",
        "biz_content": json.dumps(biz_content, ensure_ascii=False, separators=(",", ":")),
    }
    if ALIPAY_NOTIFY_URL:
        params["notify_url"] = ALIPAY_NOTIFY_URL

    unsigned = "&".join(f"{k}={params[k]}" for k in sorted(params))
    params["sign"] = _rsa_sign(unsigned)
    query = "&".join(f"{k}={quote_plus(str(v))}" for k, v in params.items())
    return f"{ALIPAY_GATEWAY}?{query}"


def verify_notify(form: dict) -> bool:
    """校验支付宝异步通知（POST 到 notify_url 的表单）的签名，防止伪造回调直接把钱刷出来。

    真实生产逻辑：验签通过 + trade_status 是 TRADE_SUCCESS/TRADE_FINISHED，才允许把
    订单标成已支付——签名不对的请求必须原样拒绝，绝不能"先信任再补验证"。
    """
    if not alipay_configured():
        return False
    from Crypto.PublicKey import RSA
    from Crypto.Signature import PKCS1_v1_5
    from Crypto.Hash import SHA256
    import base64

    data = dict(form)
    sign = data.pop("sign", "")
    data.pop("sign_type", None)
    if not sign:
        return False
    unsigned = "&".join(f"{k}={data[k]}" for k in sorted(data) if data.get(k) not in (None, ""))

    pem = "-----BEGIN PUBLIC KEY-----\n" + ALIPAY_PUBLIC_KEY.strip() + "\n-----END PUBLIC KEY-----"
    key = RSA.import_key(pem)
    h = SHA256.new(unsigned.encode("utf-8"))
    verifier = PKCS1_v1_5.new(key)
    try:
        return verifier.verify(h, base64.b64decode(sign))
    except Exception:
        return False
