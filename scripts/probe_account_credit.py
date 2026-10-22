#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""探测「账户总积分」接口（只读）。

线索：JWT 的 aud="account"，说明账户服务是独立资源服务器；
     登录态 domain=www.workbuddy.cn；客户端显示 3738.61 总积分，
     而 checkin-activity-status 的 total_credits=600 是「本期活动积分」。

本脚本只发 GET/只读 POST，探测各域下的账户/积分读接口。
"""
import json
import os
import sys

sys.path.insert(0, r"C:\Users\COSMAX\.workbuddy\skills\totorosir-workbuddy-score\scripts")
import workbuddy_checkin as wc  # noqa: E402

import importlib.util
spec = importlib.util.spec_from_file_location(
    "bs", os.path.join(os.path.dirname(os.path.abspath(__file__)), "buddy_station.py"))
bs = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bs)


def load_rt():
    for p in wc._auth_candidates():
        if os.path.isfile(p):
            try:
                with open(p, "r", encoding="utf-8") as f:
                    a = json.load(f).get("auth", {})
                if a.get("refreshToken"):
                    return wc.decrypt_access_token_field(a["refreshToken"])
            except Exception:
                continue
    raise SystemExit("无 refreshToken")


def main():
    os.environ["WB_REFRESH_TOKEN"] = load_rt()
    tok, _ = bs.acquire_access_token()

    domains = [
        "https://www.workbuddy.cn",
        "https://www.codebuddy.cn",
        "https://account.workbuddy.cn",
        "https://account.codebuddy.cn",
    ]
    paths = [
        "/v2/account/credit",
        "/v2/account/credits",
        "/v2/account/balance",
        "/v2/account/points",
        "/v2/account/user/credit",
        "/v2/account/profile",
        "/v2/account/point/balance",
        "/v2/billing/account/balance",
        "/v2/billing/point/balance",
        "/v2/billing/meter/point-balance",
        "/v2/billing/credits/balance",
        "/v2/credit/balance",
        "/v2/credits/balance",
        "/v2/point/balance",
        "/v2/user/credit",
        "/v2/user/points",
        "/v2/user/balance",
        "/v2/benefit/balance",
        "/v2/wallet/balance",
    ]

    hits = []
    for dom in domains:
        for path in paths:
            url = dom + path
            st, body = bs.http_json(url, tok, "GET")
            code = body.get("code") if isinstance(body, dict) else None
            if st == 200 and code == 0:
                d = body.get("data")
                hits.append((dom, path, d))
                print("✅ %s%s" % (dom.replace("https://", ""), path))
                if isinstance(d, dict):
                    for k, v in d.items():
                        if isinstance(v, (int, float)) and v > 50:
                            print("      └─ %s = %s" % (k, v))
                else:
                    print("      data=%s" % str(d)[:120])
    if not hits:
        print("全部未命中（HTTP 非 200/非 0 码）")


if __name__ == "__main__":
    main()