#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""只验证长效令牌刷新链路是否可用（不打印任何令牌明文）。

刷新端点与请求头取自技能实现：POST https://copilot.tencent.com/v2/plugin/auth/token/refresh
Headers: X-Refresh-Token / X-Auth-Refresh-Source: plugin / X-Domain: copilot.tencent.com
"""
import json
import os
import sys
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
            raw = auth.get("refreshToken")
            if raw is None:
                continue
            return wc.decrypt_access_token_field(raw), p
        except Exception as e:
            print("候选 %s 读取失败：%s" % (p, str(e)[:120]))
    raise SystemExit("所有候选登录态均无 refreshToken")


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


rt, path = load_rt()
print("登录态：%s" % path)
print("refresh_token 脱敏：%s（长度 %d）" % (mask(rt), len(rt)))
print("=" * 72)

st, body = refresh(rt)
print("POST %s%s" % (PLUGIN_API, REFRESH_PATH))
print("HTTP %s" % st)

# 脱敏：仅展示 code/msg 与令牌是否存在、是否轮换
code = body.get("code")
msg = body.get("msg")
data = body.get("data") if isinstance(body.get("data"), dict) else {}
at = data.get("accessToken") or ""
new_rt = data.get("refreshToken") or ""
print(json.dumps({
    "code": code,
    "msg": msg,
    "data_keys": sorted(data.keys()) if data else [],
    "accessToken_present": bool(at),
    "accessToken_masked": mask(at),
    "accessToken_len": len(at),
    "refreshToken_present": bool(new_rt),
    "refreshToken_rotated": bool(new_rt and new_rt != rt),
    "expiresIn": data.get("expiresIn"),
}, ensure_ascii=False, indent=2))

if code == 0 and at:
    print("\n>>> 刷新链路可用：新 accessToken 可直接用于签到与旅行接口。")
else:
    print("\n>>> 刷新链路不可用：需回落到 accessToken 直注方案。")
    sys.exit(1)