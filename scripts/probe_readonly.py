#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""只读接口探针：核对接口名与返回结构是否与实际请求一致。

约束：
  - 仅调用 3 个只读接口 + 1 个只读状态查询，绝不触发任何写操作。
  - 不打印 accessToken，仅打印脱敏 token 与原始 JSON 返回体。

用法：
  python probe_readonly.py                 # 自动用本机登录态
  WB_ACCESS_TOKEN=<token> python probe_readonly.py   # 用指定令牌（CI 场景）
"""

import json
import os
import sys
import urllib.error
import urllib.request

TIMEOUT = 15
UA = "WorkBuddy-BuddyStation-Probe/1.0"

# ---- 经真实请求核对的接口（不采信任何未经实测的旧接口名）----
BILLING_BASE = "https://www.codebuddy.cn/v2"
TRAVEL_BASE = "https://www.workbuddy.cn"
STATUS_PATH = "/billing/meter/checkin-activity-status"
TRAVEL_STATUS_PATH = "/activity/growth/buddy/travel/status"
TRAVEL_CONFIG_PATH = "/activity/growth/buddy/travel/config"
TRAVEL_ENERGY_PATH = "/activity/growth/energy"
TRAVEL_QUOTA_PATH = "/activity/growth/buddy/quota"


def mask(t):
    if not t:
        return "<empty>"
    return t[:6] + "..." + t[-4:]


def call(base, path, token, method="GET", payload=None):
    url = base + path
    data = None
    if method != "GET":
        data = json.dumps(payload or {}).encode("utf-8")
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", "Bearer %s" % token)
    req.add_header("Content-Type", "application/json")
    req.add_header("Accept", "application/json")
    req.add_header("User-Agent", UA)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            body = resp.read().decode("utf-8", "replace")
            try:
                return resp.status, json.loads(body)
            except json.JSONDecodeError:
                return resp.status, {"raw": body}
    except urllib.error.HTTPError as e:
        try:
            body = e.read().decode("utf-8", "replace")
            try:
                return e.code, json.loads(body)
            except json.JSONDecodeError:
                return e.code, {"raw": body}
        except Exception:
            return e.code, {"raw": ""}
    except Exception as e:
        return -1, {"error": str(e)[:200]}


def resolve_token():
    """优先环境变量（CI 场景），否则回落到本机登录态。"""
    env = os.environ.get("WB_ACCESS_TOKEN", "").strip()
    if env:
        return env, "env:WB_ACCESS_TOKEN"
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import workbuddy_checkin as wc  # 本机解密能力（仅本机可用）
    except Exception as e:
        raise SystemExit("无法加载本机登录态模块：%s" % str(e)[:160])
    token, domain, path = wc.load_token_best()
    return token, "local:%s (domain=%s)" % (path, domain)


def main():
    token, source = resolve_token()
    print("token 来源：%s" % source)
    print("token 脱敏：%s" % mask(token))
    print("=" * 72)

    probes = [
        ("状态查询（积分/连签/今日是否已记录）",
         BILLING_BASE, STATUS_PATH, "POST"),
        ("旅行状态", TRAVEL_BASE, TRAVEL_STATUS_PATH, "GET"),
        ("可选地点", TRAVEL_BASE, TRAVEL_CONFIG_PATH, "GET"),
        ("能量余额", TRAVEL_BASE, TRAVEL_ENERGY_PATH, "GET"),
        ("Buddy 配额", TRAVEL_BASE, TRAVEL_QUOTA_PATH, "GET"),
    ]

    out = {}
    for label, base, path, method in probes:
        st, body = call(base, path, token, method=method)
        print("\n### %s" % label)
        print("%s %s%s" % (method, base, path))
        print("HTTP %s" % st)
        print(json.dumps(body, ensure_ascii=False, indent=2)[:2000])
        out[label] = {"method": method, "url": base + path,
                      "http": st, "body": body}
    return 0


if __name__ == "__main__":
    sys.exit(main())