#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Buddy 加油站 · GitHub Actions 云端任务
=====================================

单次运行内完成：**签到** + **派猫猫旅行**，并把结果推送到飞书。

设计要点
--------
1. **无本地依赖**：云端 runner 没有 WorkBuddy 客户端登录态，凭据由本机
   `export_credentials.py` 导出后注入 GitHub Secret，运行时从环境变量读取。
2. **令牌自续期**：优先用长效 `refresh_token` 换取短期 `accessToken`；
   刷新失败时回落到直接注入的 `access_token`。
3. **接口以实测为准**：所有路径/方法均经真实请求核对（见 docs/API-EVIDENCE.md），
   不使用任何未经实测的旧接口名。
4. **失败隔离**：猫猫部分任何失败都不影响签到结论与整体退出码；
   只要签到成功（签到成功 / 今日已签），整体即视为成功。
5. **零明文泄露**：令牌只存在于内存与进程环境，任何输出、日志、推送内容
   均不含令牌明文（仅可能输出脱敏指纹）。

环境变量
--------
  WB_REFRESH_TOKEN   必填（推荐）长效令牌，注入 GitHub Secret
  WB_ACCESS_TOKEN    可选，短期令牌，作为刷新失败时的回落凭据
  WB_DOMAIN          可选，覆盖默认 API 域名
  FEISHU_WEBHOOK     可选，飞书机器人 webhook；缺省则跳过推送
  FEISHU_SECRET      可选，飞书机器人加签密钥

退出码
------
  0  签到成功（含「今日已签」幂等成功）；猫猫成功、部分失败、被跳过均不影响
  1  签到失败
  2  凭据缺失或令牌刷新失败（环境/配置问题，需人工介入）
"""

import base64
import hashlib
import hmac
import json
import os
import random
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

# ============================================================================
# 接口常量 —— 全部经真实请求核对（docs/API-EVIDENCE.md 存证）
# ============================================================================

# 长效令牌刷新端点（网关，与客户端刷新同一官方接口）
REFRESH_URL = "https://copilot.tencent.com/v2/plugin/auth/token/refresh"

# 签到域（注意：与旅行域不同）
BILLING_BASE = "https://www.codebuddy.cn/v2"
STATUS_PATH = "/billing/meter/checkin-activity-status"      # POST 状态查询（只读）
CHECKIN_PATH = "/billing/meter/daily-checkin"              # POST 签到（写，幂等）

# 旅行域（关键：路径不带 /v2 前缀，带了必 404）
TRAVEL_BASE = "https://www.workbuddy.cn"
TRAVEL_STATUS_PATH = "/activity/growth/buddy/travel/status"   # GET  旅行状态
TRAVEL_CONFIG_PATH = "/activity/growth/buddy/travel/config"   # GET  可选地点
TRAVEL_ENERGY_PATH = "/activity/growth/energy"               # GET  能量余额
TRAVEL_CLAIM_PATH = "/activity/growth/buddy/travel/claim"     # POST 领取（写）
TRAVEL_DEPART_PATH = "/activity/growth/buddy/travel/depart"   # POST 派出（写）

HTTP_TIMEOUT = 20
UA = "WorkBuddy-BuddyStation-Actions/1.0"

TRAVEL_STATE_TEXT = {
    "idle": "空闲（可派遣）",
    "traveling": "旅行中",
    "arrived": "已到达，待领取",
}


# ============================================================================
# 基础工具
# ============================================================================

def mask_token(t):
    """令牌脱敏：仅前 6 后 4，任何输出都必须走这里。"""
    if not t:
        return "<empty>"
    return "%s...%s (len=%d)" % (t[:6], t[-4:], len(t))


def sanitize_value(v):
    """清洗从 GitHub Secret 读入的凭据。

    Secret 的值常在手工粘贴时带入换行/空格/CR/BOM：
      · 放进 HTTP 请求头会让 http.client 抛 "Invalid header value"；
      · 放进 URL 会让飞书返回 19001（token invalid）。
    这里统一做：去 BOM → 去全部空白 → 字符集白名单过滤。

    白名单保留了 URL 所需的 : ? & = 与 base64 的 - _ ~ + / =，
    因此同一个函数可安全用于令牌与 webhook。
    """
    if not v:
        return ""
    v = v.replace("﻿", "")
    v = "".join(v.split())
    return "".join(ch for ch in v if ch.isalnum() or ch in "-_.~+/=:?&")


def http_json(url, token=None, method="GET", payload=None, extra_headers=None,
              timeout=HTTP_TIMEOUT):
    """统一 HTTP 调用。返回 (status_code, parsed_body)。

    非 2xx 也读取响应体 —— 签到「今天已签」会返回 HTTP 400 + code 10001，
    属于幂等成功，必须能读到 body 才能正确判定。
    """
    data = None
    if method != "GET":
        data = json.dumps(payload if payload is not None else {}).encode("utf-8")
    req = urllib.request.Request(url, data=data, method=method)
    if token:
        req.add_header("Authorization", "Bearer %s" % token)
    req.add_header("Content-Type", "application/json")
    req.add_header("Accept", "application/json")
    req.add_header("User-Agent", UA)
    for k, v in (extra_headers or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", "replace")
            try:
                return resp.status, json.loads(raw)
            except json.JSONDecodeError:
                return resp.status, {"raw": raw}
    except urllib.error.HTTPError as e:
        try:
            raw = e.read().decode("utf-8", "replace")
            try:
                return e.code, json.loads(raw)
            except json.JSONDecodeError:
                return e.code, {"raw": raw}
        except Exception:
            return e.code, {}
    except Exception as e:
        return -1, {"error": str(e)[:200]}


# ============================================================================
# 令牌获取：长效优先，短期回落
# ============================================================================

def acquire_access_token():
    """取得可用的 accessToken。

    返回 (token, source_desc)。失败抛 RuntimeError。
    令牌不写入任何输出，仅返回给调用方在内存中使用。
    """
    refresh_token = sanitize_value(os.environ.get("WB_REFRESH_TOKEN", ""))
    fallback_token = sanitize_value(os.environ.get("WB_ACCESS_TOKEN", ""))

    if not refresh_token and not fallback_token:
        raise RuntimeError(
            "缺少凭据：请注入 WB_REFRESH_TOKEN（推荐）或 WB_ACCESS_TOKEN。"
            "在本机运行 scripts/export_credentials.py 可一键导出并写入 GitHub Secret。")

    if refresh_token:
        status, body = http_json(
            REFRESH_URL, method="POST", payload={},
            extra_headers={
                "X-Refresh-Token": refresh_token,
                "X-Auth-Refresh-Source": "plugin",
                "X-Domain": "copilot.tencent.com",
            },
        )
        data = body.get("data") if isinstance(body, dict) else None
        at = (data or {}).get("accessToken") or ""
        if status == 200 and isinstance(body, dict) and body.get("code") == 0 and at:
            return at, "refresh_token 刷新（%s）" % mask_token(at)
        # 刷新失败：若备了短期令牌则回落
        # 诊断信息：网络层异常时 body 只会带error 字段，必须单独取出，
        # 否则真实原因（DNS/超时/TLS）会被吞掉，只看到一个无意义的 HTTP -1。
        diag = (body or {}).get("error") or ""
        code = (body or {}).get("code")
        msg = (body or {}).get("msg") or ""
        detail = "HTTP %s code=%s msg=%s%s" % (
            status, code, msg, ("（%s）" % diag) if diag else "")

        if fallback_token:
            sys.stderr.write(
                "[token] 长效令牌刷新失败（%s），回落到 WB_ACCESS_TOKEN。\n" % detail)
            return fallback_token, "WB_ACCESS_TOKEN 直注（回落）"

        hint = ""
        if status == -1:
            if "Invalid header value" in diag:
                hint = ("；请求头被拒，通常是 Secret 粘贴时带入了换行/空格。"
                        "请重新配置 WB_REFRESH_TOKEN，确保粘贴为单行纯文本")
            else:
                hint = ("；这通常是网络层问题：GitHub 机房到 copilot.tencent.com 的网络受阻，"
                        "或该域名被 DNS/防火墙拦截。令牌本身无误时也会出现此码")
        raise RuntimeError(
            "长效令牌刷新失败：%s%s。请在本机重新运行 "
            "scripts/export_credentials.py 更新 GitHub Secret。" % (detail, hint))

    return fallback_token, "WB_ACCESS_TOKEN 直注"


# ============================================================================
# 签到（整体成败的唯一判定依据）
# ============================================================================

def fetch_status(token):
    """查加油站状态。返回 (ok, data_or_error)。"""
    status, body = http_json(BILLING_BASE + STATUS_PATH, token, "POST", {})
    if status == 200 and isinstance(body, dict) and body.get("code") == 0:
        return True, body.get("data") or {}
    return False, {"http": status, "code": (body or {}).get("code"),
                   "msg": (body or {}).get("msg")}


def do_checkin(token):
    """执行签到。

    幂等策略：先查状态，已记录则不发写请求；未记录才 POST。
    HTTP 400 + code 10001「今天已签到」视为幂等成功。

    返回 dict：ok / skipped / failed
    """
    ok, data = fetch_status(token)
    if not ok:
        return {"result": "failed",
                "message": "状态查询失败（HTTP %s code=%s msg=%s）"
                           % (data.get("http"), data.get("code"), data.get("msg"))}

    if data.get("today_checked_in"):
        return {"result": "skipped",
                "message": "今日已签到，无需重复签到",
                "credits": data.get("total_credits"),
                "streak": data.get("streak_days")}

    status, body = http_json(BILLING_BASE + CHECKIN_PATH, token, "POST", {})
    code = body.get("code") if isinstance(body, dict) else None
    msg = (body or {}).get("msg") or ""

    if status == 200 and code == 0:
        return {"result": "ok", "message": "签到成功",
                "credit": (body.get("data") or {}).get("credit")}
    # 幂等：HTTP 400 + code 10001 = 今天已签到
    if code == 10001:
        return {"result": "skipped", "message": "今日已签到（幂等）%s" % msg}
    return {"result": "failed",
            "message": "签到失败（HTTP %s code=%s %s）" % (status, code, msg)}


# ============================================================================
# 派猫猫旅行（先领后派；失败绝不抛出，隔离在自身结果内）
# ============================================================================

def travel_get(token, path):
    """旅行只读 GET。失败返回 None（静默降级，不影响签到）。"""
    status, body = http_json(TRAVEL_BASE + path, token, "GET")
    if status == 200 and isinstance(body, dict) and body.get("code") == 0:
        return body.get("data")
    return None


def travel_write(token, path, payload):
    """旅行写操作（claim / depart）。返回 (ok, data, msg)。"""
    status, body = http_json(TRAVEL_BASE + path, token, "POST", payload)
    msg = (body or {}).get("msg") or ""
    ok = status == 200 and isinstance(body, dict) and body.get("code") == 0
    return ok, (body.get("data") or {}), msg or ("HTTP %s" % status)


def run_travel(token):
    """派猫猫旅行闭环：先领取已到家的积分，再判断能否派新一趟。

    绝不抛异常 —— 任何失败都收敛到本函数的返回值里。
    """
    result = {"available": False, "ok": False, "steps": [],
              "state": None, "state_text": None, "location": None,
              "remaining_text": None, "reward_credit": None,
              "energy": None, "letter": None}

    status = travel_get(token, TRAVEL_STATUS_PATH)
    if not isinstance(status, dict):
        result["steps"].append("❌ 查询旅行状态失败，已跳过全部旅行写操作")
        result["error"] = "旅行状态接口不可用"
        return result

    result["available"] = True

    # --- 1. 先领取已到家的旅行积分 ---
    if status.get("state") == "arrived":
        credit_hint = status.get("reward_credit")
        ok, data, msg = travel_write(token, TRAVEL_CLAIM_PATH, {})
        if ok:
            got = data.get("reward_credit")
            result["steps"].append(
                "✅ 领取旅行积分 +%s（到账前显示 %s）"
                % (got if got is not None else "?",
                   credit_hint if credit_hint is not None else "?"))
            result["claimed_credit"] = got
            letter = data.get("letter")
            if isinstance(letter, dict):
                result["letter"] = letter.get("text")
            fresh = travel_get(token, TRAVEL_STATUS_PATH)
            if isinstance(fresh, dict):
                status = fresh
        else:
            result["steps"].append("⚠️ 领取旅行积分失败：%s" % msg)
    else:
        result["steps"].append("ℹ️ 当前无已到达行程，无需领取")

    # --- 2. 判断是否可派新一趟 ---
    if status.get("daily_limit_reached"):
        result["steps"].append("⏭ 今日派遣已达上限，跳过派遣（不发写请求）")
    elif status.get("state") in (None, "", "idle"):
        config = travel_get(token, TRAVEL_CONFIG_PATH) or {}
        locs = config.get("locations") or []
        ids = [x.get("id") for x in locs if isinstance(x, dict) and x.get("id") is not None]
        if not ids:
            result["steps"].append("⚠️ 未取到可选地点，跳过派遣")
        else:
            chosen = random.choice(ids)
            ok, data, msg = travel_write(token, TRAVEL_DEPART_PATH,
                                         {"location_id": chosen})
            if ok:
                loc = data.get("location") or {}
                name = loc.get("name") or ("地点 %s" % chosen)
                result["steps"].append("✅ 已派 Buddy 前往【%s】" % name)
                result["departed"] = True
                result["depart_location"] = name
                result["arrive_at"] = data.get("arrive_at")
                letter = data.get("letter")
                if isinstance(letter, dict):
                    result["letter"] = letter.get("text")
                fresh = travel_get(token, TRAVEL_STATUS_PATH)
                if isinstance(fresh, dict):
                    status = fresh
            else:
                result["steps"].append("⚠️ 派遣失败：%s" % msg)
    elif status.get("state") == "traveling":
        result["steps"].append("⏭ Buddy 正在旅行中，无需重复派遣")
    else:
        result["steps"].append("⏭ 状态 %s，暂不派遣" % status.get("state"))

    # --- 3. 汇总最终状态 ---
    final = status
    st = final.get("state")
    result["state"] = st
    result["state_text"] = TRAVEL_STATE_TEXT.get(st, st or "未知")
    loc = final.get("location")
    if isinstance(loc, dict):
        result["location"] = loc.get("name")
    result["reward_credit"] = final.get("reward_credit")

    # 倒计时：仅 traveling 时计算
    if st == "traveling":
        arrive, now = final.get("arrive_at"), final.get("server_now")
        if isinstance(arrive, int) and isinstance(now, int):
            result["remaining_text"] = fmt_duration(max(0, arrive - now))

    energy = travel_get(token, TRAVEL_ENERGY_PATH)
    if isinstance(energy, dict):
        result["energy"] = energy.get("balance")

    if isinstance(final.get("letter"), dict) and not result["letter"]:
        result["letter"] = final["letter"].get("text")

    result["ok"] = not any(s.startswith(("❌", "⚠️")) for s in result["steps"])
    return result


def fmt_duration(seconds):
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m = rem // 60
    if h > 0:
        return "%d 小时 %d 分" % (h, m)
    if m > 0:
        return "%d 分钟" % m
    return "%d 秒" % seconds


# ============================================================================
# 飞书推送：签到结果与猫猫状态分开展示
# ============================================================================

def feishu_sign(secret, timestamp):
    """飞书加签：base64(hmac_sha256(key=string_to_sign, msg=空))。

    string_to_sign = f"{timestamp}\\n{secret}"，timestamp 为秒。

    注意：飞书的 HMAC 方向与钉钉/企业微信相反——这里「待签字符串」是
    **key**，消息体为空。写成 key=secret / msg=string_to_sign 会得到
    19021（sign match fail），此实现已用真实 webhook 实测通过（code=0）。
    """
    string_to_sign = ("%s\n%s" % (timestamp, secret)).encode("utf-8")
    return base64.b64encode(
        hmac.new(string_to_sign, b"", digestmod=hashlib.sha256).digest()
    ).decode("utf-8")


def build_card(report):
    """构造飞书交互卡片：签到区块与猫猫区块用分割线明确隔开。"""
    ci = report["checkin"]
    tv = report["travel"]

    ci_icon = {"ok": "✅", "skipped": "☑️", "failed": "❌"}.get(ci["result"], "❓")
    ci_text = {
        "ok": "签到成功",
        "skipped": "今日已签（幂等跳过写请求）",
        "failed": "签到失败",
    }[ci["result"]]

    lines_ci = [
        "**%s %s**" % (ci_icon, ci_text),
        "· 明细：%s" % ci["message"],
        "· 积分余额：%s" % (ci.get("credits") if ci.get("credits") is not None else "—"),
        "· 连续签到：%s 天" % (ci.get("streak") if ci.get("streak") is not None else "—"),
    ]

    if tv.get("available"):
        tv_title = "🐱 猫猫旅行（%s）" % (tv.get("state_text") or "状态未知")
        lines_tv = ["**%s**" % tv_title]
        if tv.get("location"):
            lines_tv.append("· 地点：%s" % tv["location"])
        if tv.get("remaining_text"):
            lines_tv.append("· 到达倒计时：还需 %s" % tv["remaining_text"])
        if tv.get("reward_credit") is not None:
            lines_tv.append("· 可领积分：%s" % tv["reward_credit"])
        if tv.get("energy") is not None:
            lines_tv.append("· 能量余额：%s" % tv["energy"])
        for s in tv.get("steps", []):
            lines_tv.append("· %s" % s)
        if tv.get("letter"):
            snippet = tv["letter"].replace("\n", " ")[:120]
            lines_tv.append("· Buddy 手记：%s…" % snippet)
    else:
        lines_tv = ["**🐱 猫猫旅行**", "· 状态查询失败：%s" % tv.get("error", "未知原因"),
                    "· ⚠️ 猫猫部分失败不影响签到结论"]

    color = "green" if report["overall_ok"] else "red"
    title = "Buddy 加油站 · %s" % (
        "每日签到 + 派猫猫" if report["overall_ok"] else "签到失败")

    return {
        "msg_type": "interactive",
        "card": {
            "config": {"wide_screen_mode": True},
            "header": {
                "template": color,
                "title": {"tag": "plain_text", "content": title},
            },
            "elements": [
                {"tag": "div",
                 "text": {"tag": "lark_md", "content": "\n".join(lines_ci)}},
                {"tag": "hr"},
                {"tag": "div",
                 "text": {"tag": "lark_md", "content": "\n".join(lines_tv)}},
                {"tag": "hr"},
                {"tag": "note", "elements": [{"tag": "plain_text",
                                              "content": "GitHub Actions · %s"
                                                         % report["finished_at"]}]},
            ],
        },
    }


def push_feishu(report):
    """推送飞书。未配置 webhook 则跳过。返回 (status, detail)。"""
    # 同样做彻底清洗：webhook 复制时也可能带入换行/空格，
    # 与 Secret 粘贴是同一类问题，不清洗会得到 19001 token invalid。
    webhook = sanitize_value(os.environ.get("FEISHU_WEBHOOK", ""))
    if not webhook:
        return "skipped", "未配置 FEISHU_WEBHOOK，跳过推送"

    secret = sanitize_value(os.environ.get("FEISHU_SECRET", ""))
    if secret:
        # 飞书加签：把 timestamp / sign 作为 query 参数追加。
        # 用 urllib.parse 构造而非手工字符串拼接——手工拼 '&' 时若原 URL
        # 没有 '?'，会拼出 ".../hook/UUID&timestamp=..." 这种畸形 URL，
        # urllib 会因首个 '&' 前无 '?' 而抛 unknown url type。
        ts = str(int(time.time()))
        sign = feishu_sign(secret, ts)
        sep = "&" if "?" in webhook else "?"
        webhook = "%s%stimestamp=%s&sign=%s" % (
            webhook, sep, urllib.parse.quote(ts), urllib.parse.quote(sign))

    card = build_card(report)
    try:
        with urllib.request.urlopen(
                urllib.request.Request(
                    webhook,
                    data=json.dumps(card, ensure_ascii=False).encode("utf-8"),
                    method="POST",
                    headers={"Content-Type": "application/json"}),
                timeout=15) as resp:
            body = json.loads(resp.read().decode("utf-8", "replace"))
            if body.get("code") == 0 or body.get("StatusCode") == 0:
                return "ok", "推送成功"
            return "failed", "飞书返回 code=%s msg=%s" % (body.get("code"), body.get("msg"))
    except urllib.error.HTTPError as e:
        return "failed", "飞书 HTTP %s" % e.code
    except Exception as e:
        return "failed", "推送异常 %s" % str(e)[:120]


# ============================================================================
# 主流程
# ============================================================================

def main():
    started = time.time()
    report = {
        "started_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "token_source": None,
        "checkin": {"result": "failed", "message": "未执行"},
        "travel": {"available": False, "steps": []},
    }

    # ---- 0. 取凭据 ----
    try:
        token, source = acquire_access_token()
        report["token_source"] = source
        print("[token] %s" % source)
    except RuntimeError as e:
        report["checkin"] = {"result": "failed", "message": str(e)}
        report["overall_ok"] = False
        _emit(report, 2)
        return 2

    # ---- 1. 签到（整体成败唯一依据）----
    report["checkin"] = do_checkin(token)
    print("[签到] %s —— %s" % (report["checkin"]["result"], report["checkin"]["message"]))

    # 补齐积分/连签展示（签到写接口不回传时从状态查询取）
    if report["checkin"].get("credits") is None:
        ok, data = fetch_status(token)
        if ok:
            report["checkin"]["credits"] = data.get("total_credits")
            report["checkin"]["streak"] = data.get("streak_days")

    # ---- 2. 派猫猫旅行（失败隔离，绝不影响签到结论）----
    try:
        report["travel"] = run_travel(token)
        for s in report["travel"].get("steps", []):
            print("[旅行] %s" % s)
        print("[旅行] 最终状态：%s%s" % (
            report["travel"].get("state_text") or "-",
            (" @ " + report["travel"]["location"]) if report["travel"].get("location") else ""))
    except Exception as e:  # 兜底：猫猫部分任何异常都不上抛
        report["travel"] = {"available": False, "steps": ["❌ 旅行模块异常：%s" % str(e)[:120]],
                            "error": str(e)[:120]}
        print("[旅行] 异常（已隔离，不影响签到）：%s" % str(e)[:120])

    # ---- 3. 整体结论：只看签到 ----
    report["overall_ok"] = report["checkin"]["result"] in ("ok", "skipped")

    # ---- 4. 推送飞书 ----
    report["finished_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    pstatus, pdetail = push_feishu(report)
    report["push"] = {"status": pstatus, "detail": pdetail}
    print("[推送] %s —— %s" % (pstatus, pdetail))

    report["duration_sec"] = round(time.time() - started, 1)
    code = 0 if report["overall_ok"] else 1
    _emit(report, code)
    return code


def _emit(report, code):
    print("\n" + "=" * 60)
    print("整体结论：%s（签到=%s，猫猫=%s）"
          % ("成功" if report.get("overall_ok") else "失败",
             report["checkin"]["result"],
             "成功" if report["travel"].get("ok") else "部分失败/跳过"))
    print("=" * 60)
    # 机器可读结果（供 Actions 后续步骤/工件使用），不含任何令牌明文
    print("RESULT_JSON_START")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print("RESULT_JSON_END")
    sys.exit(code)


if __name__ == "__main__":
    sys.exit(main())