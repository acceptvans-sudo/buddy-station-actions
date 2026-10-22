#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""实测长效令牌轮换行为：轮换后旧 refresh_token 是否立即失效。

这决定 GitHub Actions 架构：
  - 若旧令牌立即失效 → 每次运行都会拿到新令牌，必须有回写机制，
    否则第二天 cron 就会失败。
  - 若旧令牌在宽限期内仍可用 → 直接存 secret 即可。
"""
import json
import os
import sys
import time
import urllib.error
import urllib.request

sys.path.insert(0, r"C:\Users\COSMAX\.workbuddy\skills\totorosir-workbuddy-score\scripts")
import workbuddy_checkin as wc  # noqa: E402

PLUGIN_API = "https://copilot.tencent.com"
REFRESH_PATH = "/v2/plugin/auth/token/refresh"


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


def refresh(rt):
    req = urllib.request.Request(PLUGIN_API + REFRESH_PATH, data=b"{}", method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("X-Refresh-Token", rt)
    req.add_header("X-Auth-Refresh-Source", "plugin")
    req.add_header("X-Domain", "copilot.tencent.com")
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode("utf-8", "replace"))
        except Exception:
            return e.code, {"raw": ""}
    except Exception as e:
        return -1, {"error": str(e)[:200]}


rt0 = load_rt()
print("初始 refresh_token：%s（len %d）" % (mask(rt0), len(rt0)))

# 第 1 次刷新
st1, b1 = refresh(rt0)
d1 = b1.get("data") or {}
rt1 = d1.get("refreshToken") or ""
print("\n[第1次刷新] HTTP %s code=%s msg=%s" % (st1, b1.get("code"), b1.get("msg")))
print("  新令牌：%s（len %d） 轮换=%s" % (mask(rt1), len(rt1), rt1 != rt0))
if "refreshExpiresIn" in d1:
    print("  refreshExpiresIn=%s（≈%.1f 天）" % (d1["refreshExpiresIn"], d1["refreshExpiresIn"] / 86400))
if "expiresIn" in d1:
    print("  accessToken expiresIn=%s（≈%.1f 天）" % (d1["expiresIn"], d1["expiresIn"] / 86400))

# 关键：用「已经被轮换掉的旧令牌 rt0」再刷新一次
time.sleep(1)
st2, b2 = refresh(rt0)
print("\n[用旧令牌 rt0 再次刷新] HTTP %s code=%s msg=%s" % (st2, b2.get("code"), b2.get("msg")))
ok2 = b2.get("code") == 0 and bool((b2.get("data") or {}).get("accessToken"))
print("  旧令牌是否仍可用：%s" % ("是（存在宽限期）" if ok2 else "否（立即失效）"))

# 第 3 次：用 rt1 刷新，确认链式轮换可持续
if rt1:
    st3, b3 = refresh(rt1)
    d3 = b3.get("data") or {}
    rt2 = d3.get("refreshToken") or ""
    print("\n[用 rt1 刷新] HTTP %s code=%s → 再次轮换=%s"
          % (st3, b3.get("code"), bool(rt2 and rt2 != rt1)))
    print("\n结论：轮换链可持续=%s" % ("是" if b3.get("code") == 0 else "否"))