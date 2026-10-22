#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""探测「账户总积分余额」的真实接口（只读）。

背景：checkin-activity-status 返回的 total_credits=600 是「本期加油站活动
积分」（6 天 x 100 分），而客户端显示的 3738.61 是账户总积分。
本脚本只发 GET/只读 POST，逐个试探候选路径，找到返回总余额的那个。

安全：全程只读，不调用任何写接口；令牌不打印。
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

    base = "https://www.codebuddy.cn"
    # 候选路径：覆盖 billing meter 的各种读法
    cands = [
        ("GET", "/v2/billing/meter/credit-balance"),
        ("GET", "/v2/billing/meter/credits"),
        ("GET", "/v2/billing/meter/credit"),
        ("GET", "/v2/billing/meter/balance"),
        ("GET", "/v2/billing/meter/user-credit"),
        ("GET", "/v2/billing/meter/account"),
        ("GET", "/v2/billing/meter/user-info"),
        ("GET", "/v2/billing/credit/balance"),
        ("GET", "/v2/billing/credits/balance"),
        ("GET", "/v2/billing/meter/point/balance"),
        ("GET", "/v2/billing/meter/summary"),
        ("GET", "/v2/billing/meter/checkin-status"),
        ("POST", "/v2/billing/meter/credit-balance"),
        ("POST", "/v2/billing/meter/credits"),
        ("POST", "/v2/billing/meter/balance"),
        ("POST", "/v2/billing/meter/user-credit"),
        ("POST", "/v2/billing/meter/account"),
        ("POST", "/v2/billing/meter/point"),
    ]

    for method, path in cands:
        url = base + path
        st, body = bs.http_json(url, tok, method, {} if method == "POST" else None)
        code = body.get("code") if isinstance(body, dict) else None
        if st == 200 and code == 0:
            d = body.get("data")
            keys = list(d.keys())[:12] if isinstance(d, dict) else str(d)[:60]
            print("✅ %-6s %-42s code=0 data=%s" % (method, path, keys))
            # 打印可能含余额的字段
            if isinstance(d, dict):
                for k, v in d.items():
                    if isinstance(v, (int, float)) and v > 100:
                        print("      └─ %s = %s" % (k, v))
        else:
            print("   %-6s %-42s HTTP %s code=%s" % (method, path, st, code))


if __name__ == "__main__":
    main()