#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""验证「长效令牌刷新出的 accessToken」能否直接驱动签到/旅行接口。

只调用只读接口验证令牌有效性，不触发任何写操作。
"""
import json
import os
import sys
import urllib.error
import urllib.request

sys.path.insert(0, r"C:\Users\COSMAX\.workbuddy\skills\totorosir-workbuddy-score\scripts")
import workbuddy_checkin as wc  # noqa: E402


def mask(t):
    return (t[:6] + "..." + t[-4:]) if t else "<empty>"


def load_rt():
    for p in wc._auth_candidates():
        if not os.path.isfile(p):
            continue
        try:
            with open(p, "r", encoding="utf-8") as f:
                auth = json.load(f).get("auth", {})
            if auth.get("refreshToken"):
                return wc.decrypt_access_token_field(auth["refreshToken"])
        except Exception:
            continue
    raise SystemExit("无 refreshToken")


def req(url, token, method="GET", payload=None, headers=None):
    data = None if method == "GET" else json.dumps(payload or {}).encode("utf-8")
    r = urllib.request.Request(url, data=data, method=method)
    r.add_header("Authorization", "Bearer %s" % token)
    r.add_header("Content-Type", "application/json")
    r.add_header("Accept", "application/json")
    for k, v in (headers or {}).items():
        r.add_header(k, v)
    try:
        with urllib.request.urlopen(r, timeout=15) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode("utf-8", "replace"))
        except Exception:
            return e.code, {}
    except Exception as e:
        return -1, {"error": str(e)[:150]}


rt = load_rt()
st, body = req("https://copilot.tencent.com/v2/plugin/auth/token/refresh", "",
               "POST", {}, {"X-Refresh-Token": rt,
                            "X-Auth-Refresh-Source": "plugin",
                            "X-Domain": "copilot.tencent.com"})
d = body.get("data") or {}
at = d.get("accessToken") or ""
print("刷新：HTTP %s code=%s  domain=%s" % (st, body.get("code"), d.get("domain")))
print("新 accessToken：%s（len %d）\n" % (mask(at), len(at)))

# 用刷新出的令牌访问两个域名的只读接口
checks = [
    ("签到状态查询", "https://www.codebuddy.cn/v2/billing/meter/checkin-activity-status", "POST", None),
    ("旅行状态", "https://www.workbuddy.cn/activity/growth/buddy/travel/status", "GET", None),
]
ok_all = True
for label, url, method, payload in checks:
    s, b = req(url, at, method, payload)
    ok = s == 200 and b.get("code") == 0
    ok_all = ok_all and ok
    print("%s → HTTP %s code=%s %s" % (label, s, b.get("code"), "✅" if ok else "❌"))
    data = b.get("data") or {}
    if "travel" in label:
        print("   state=%s reward_credit=%s daily_limit_reached=%s"
              % (data.get("state"), data.get("reward_credit"), data.get("daily_limit_reached")))
    else:
        print("   today_checked_in=%s streak_days=%s total_credits=%s"
              % (data.get("today_checked_in"), data.get("streak_days"), data.get("total_credits")))

print("\n结论：刷新令牌可直接驱动业务接口 = %s" % ("是" if ok_all else "否"))
sys.exit(0 if ok_all else 1)