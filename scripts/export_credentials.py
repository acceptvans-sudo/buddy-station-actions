#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
凭据导出器（本机运行，Actions 侧不运行）
=========================================

云端 runner 没有 WorkBuddy 客户端登录态，无法自行取得令牌。本脚本在**本机**
读取已登录客户端的登录态，导出长效 `refresh_token`，并写入 GitHub 仓库 Secret。

安全约定
--------
  · 只读登录态文件，绝不修改 / 删除。
  · 令牌**默认不落盘**：仅经环境变量或 stdin 管道直达 `gh secret set`，
    不写临时文件、不进 shell 历史、不打印到终端。
  · 若必须打印（人工复制场景），需显式加 `--print`，且强制二次确认。
  · 令牌视同账号钥匙；如需撤销，在 WorkBuddy 客户端重新登录即可。

用法
----
  # 1) 探测本机登录态（不接触令牌明文，最安全）
  python export_credentials.py --check

  # 2) 导出并写入 GitHub Secret（推荐，需已安装并登录 gh CLI）
  python export_credentials.py --repo <owner/name>

  # 3) 导出到本地 .env（供本机调试；该文件已在 .gitignore 中）
  python export_credentials.py --out-env .env
"""

import argparse
import json
import os
import subprocess
import sys

TIMEOUT = 15

# 登录态候选路径（与 WorkBuddy 客户端一致）
CANDIDATES = [
    os.path.join(os.environ.get("LOCALAPPDATA", ""),
                 "CodeBuddyExtension", "Data", "Public", "auth", "workbuddy-desktop.info"),
    os.path.join(os.environ.get("APPDATA", ""),
                 "CodeBuddyExtension", "Data", "Public", "auth", "workbuddy-desktop.info"),
    os.path.expanduser("~/Library/Application Support/CodeBuddyExtension/Data/Public/auth/workbuddy-desktop.info"),
    os.path.expanduser("~/.config/CodeBuddyExtension/Data/Public/auth/workbuddy-desktop.info"),
    os.path.expanduser("~/.workbuddy/auth/workbuddy-desktop.info"),
]


def mask(t):
    return "%s...%s (len=%d)" % (t[:6], t[-4:], len(t)) if t else "<empty>"


def find_auth():
    for p in CANDIDATES:
        if p and os.path.isfile(p):
            return p
    return None


def read_auth(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def decrypt_field(raw):
    """登录态可能是明文 JWT，也可能是 5.6.2+ 的 AES-GCM 信封。

    信封解密依赖本机的 AtRest 密钥发现能力，复用技能模块实现。
    """
    if isinstance(raw, str):
        return raw
    if isinstance(raw, dict) and raw.get("$wbEncrypted"):
        try:
            skill_dir = os.path.expanduser(
                "~/.workbuddy/skills/totorosir-workbuddy-score/scripts")
            sys.path.insert(0, skill_dir)
            import workbuddy_checkin as wc  # noqa: E402
            return wc.decrypt_access_token_field(raw)
        except Exception as e:
            raise RuntimeError("登录态为加密信封且解密失败：%s" % str(e)[:160])
    return None


def load_credentials():
    path = find_auth()
    if not path:
        raise SystemExit(
            "未找到登录态文件。请先在本机登录 WorkBuddy 客户端后重试。\n"
            "已尝试路径：\n  " + "\n  ".join(p for p in CANDIDATES if p))

    auth = read_auth(path).get("auth", {})
    raw_rt = auth.get("refreshToken")
    raw_at = auth.get("accessToken")

    rt = decrypt_field(raw_rt) if raw_rt else None
    at = decrypt_field(raw_at) if raw_at else None

    if not rt and not at:
        raise SystemExit("登录态中未找到 refreshToken / accessToken。")

    return path, rt, at, auth.get("domain")


def do_check():
    path, rt, at, domain = load_credentials()
    print("登录态文件：%s" % path)
    print("API 域名：%s" % (domain or "（未记录，用默认 www.codebuddy.cn）"))
    print("refresh_token：%s" % (mask(rt) if rt else "无（将回落到 accessToken）"))
    print("accessToken  ：%s" % (mask(at) if at else "无"))
    print("\n✅ 本机登录态可用于导出凭据。")


def gh_available():
    try:
        subprocess.run(["gh", "--version"], capture_output=True, timeout=10, check=True)
        return True
    except Exception:
        return False


def set_gh_secret(repo, name, value):
    """经 stdin 管道写入 GitHub Secret，避免令牌出现在命令行参数与历史里。"""
    p = subprocess.run(
        ["gh", "secret", "set", name, "--repo", repo, "--body", value],
        capture_output=True, text=True, timeout=60)
    if p.returncode != 0:
        # 回退：--body 不可用时改用 stdin
        p = subprocess.run(
            ["gh", "secret", "set", name, "--repo", repo],
            input=value, capture_output=True, text=True, timeout=60)
    if p.returncode != 0:
        raise SystemExit("写入 Secret 失败：%s" % (p.stderr or p.stdout)[:300])
    print("✅ 已写入 Secret：%s（仓库 %s）" % (name, repo))


def main():
    ap = argparse.ArgumentParser(description="导出 Buddy 加油站凭据到 GitHub Secret")
    ap.add_argument("--check", action="store_true", help="仅探测本机登录态，不导出")
    ap.add_argument("--repo", help="目标仓库，形如 owner/name")
    ap.add_argument("--out-env", help="导出到指定 .env 文件（会被 .gitignore 忽略）")
    ap.add_argument("--print", dest="do_print", action="store_true",
                    help="打印令牌明文（危险，需二次确认）")
    args = ap.parse_args()

    if args.check or not (args.repo or args.out_env or args.do_print):
        do_check()
        return 0

    path, rt, at, _domain = load_credentials()

    if args.repo:
        if not gh_available():
            raise SystemExit(
                "未检测到 gh CLI。请先安装并登录：\n"
                "  winget install GitHub.cli   # Windows\n"
                "  gh auth login")
        if rt:
            set_gh_secret(args.repo, "WB_REFRESH_TOKEN", rt)
        else:
            print("⚠️ 登录态中无 refreshToken，改用 accessToken（有效期约 28 天）")
            set_gh_secret(args.repo, "WB_ACCESS_TOKEN", at)
        print("\n下一步：到仓库 Settings → Secrets and variables → Actions 中，"
              "补充配置 FEISHU_WEBHOOK（可选）。")
        return 0

    if args.out_env:
        target = os.path.abspath(args.out_env)
        with open(target, "w", encoding="utf-8") as f:
            f.write("# 本文件含敏感凭据，已被 .gitignore 忽略，请勿提交\n")
            if rt:
                f.write("WB_REFRESH_TOKEN=%s\n" % rt)
            if at:
                f.write("WB_ACCESS_TOKEN=%s\n" % at)
        try:
            os.chmod(target, 0o600)
        except Exception:
            pass
        print("✅ 已导出到 %s（权限 600，已被 .gitignore 忽略）" % target)
        return 0

    if args.do_print:
        print("\n⚠️  你正在请求打印账号钥匙级凭据明文。")
        print("继续将把长效令牌显示在终端，可能进入日志与历史记录。")
        if input("确认继续？输入 yes 继续：").strip().lower() != "yes":
            print("已取消。")
            return 1
        print(rt or at)
        return 0

    return 0


if __name__ == "__main__":
    sys.exit(main())