#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
浏览器自动化抓取模块(攻克反爬/动态渲染网站)
================================================
用 Playwright 无头浏览器抓取普通 HTTP 请求拿不到的站点:
  · 康文署 LCSD 招标/报价邀请(展览行业最重要的源!含不上宪报的报价邀请)
  · 西九 WKProcure(尝试;若被防火墙拦会自动跳过)

本模块是可选的:
  - 本地没装 Playwright → collector.py 自动跳过,其余源照常工作
  - GitHub Actions 上会自动安装并启用(见 .github/workflows/daily.yml)

本地想启用的话:
    pip install playwright
    python -m playwright install chromium
"""

import re

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")


def _mk(source, org, ref, title, closing, link, issue=""):
    return {
        "source": source, "org": org, "ref": ref, "title": title,
        "title_en": "", "issue_date": issue, "closing": closing,
        "link": link, "contact": "",
    }


def fetch_lcsd(ctx):
    """康文署 招标/报价邀请公告(动态渲染页)
    页面格式: 截止日期<TAB>项目<TAB>编号,分「收入类」和「支出类」"""
    out = []
    pg = ctx.new_page()
    try:
        pg.goto("https://www.lcsd.gov.hk/clpss/tc/webApp/Tender.do",
                timeout=45000, wait_until="domcontentloaded")
        pg.wait_for_timeout(5000)
        txt = pg.inner_text("body")
        # 行格式: 2026-10-08\t報價承投...\tLC/LS/Q/CP/...
        for m in re.finditer(
                r"(\d{4}-\d{2}-\d{2})\t([^\t\n]{8,200})\t([A-Z][A-Za-z0-9/()\- .]{4,60})",
                txt):
            closing, title, ref = m.group(1), m.group(2).strip(), m.group(3).strip()
            out.append(_mk("康文署 (LCSD)", "康乐及文化事务署", ref, title,
                           closing,
                           "https://www.lcsd.gov.hk/clpss/tc/webApp/Tender.do"))
    finally:
        pg.close()
    return out


def fetch_wkprocure(ctx):
    """西九 WKProcure 标书列表(有防火墙,能过就赚到)"""
    out = []
    pg = ctx.new_page()
    try:
        pg.goto("https://wkprocure.westkowloon.hk/Guest/en/Tender/List.aspx?Page=1",
                timeout=40000, wait_until="domcontentloaded")
        pg.wait_for_timeout(6000)
        txt = pg.inner_text("body")
        if len(txt) < 300:          # 被防火墙拦截时页面几乎为空
            return out
        # 行格式: n. | WKCDA26/0064/HPM | Subject | Issued | 2026/06/23 (Tue) | 2026/07/21 (Tue) 16:00
        rows = pg.eval_on_selector_all(
            "table tr",
            "els => els.map(e => e.innerText.trim())")
        for r in rows:
            cells = [c.strip() for c in r.split("\t") if c.strip()]
            if len(cells) >= 5 and re.match(r"WKCDA\d{2}/\d{4}", cells[1] if len(cells) > 1 else ""):
                ref, subject = cells[1], cells[2]
                status = cells[3] if len(cells) > 3 else ""
                closing = ""
                m = re.search(r"(\d{4}/\d{2}/\d{2}).*?(\d{2}:\d{2})?$", cells[-1])
                if m:
                    closing = m.group(1).replace("/", "-") + \
                              ((" " + m.group(2)) if m.group(2) else "")
                if "Issued" in status:
                    out.append(_mk("西九文化区 WKProcure (M+/故宫馆)",
                                   "西九文化区管理局", ref, subject, closing,
                                   "https://wkprocure.westkowloon.hk/Guest/en/Tender/List.aspx"))
    finally:
        pg.close()
    return out


def fetch_all():
    """入口:返回 (标书列表, 状态列表)。Playwright 未安装时抛 ImportError。"""
    from playwright.sync_api import sync_playwright

    tenders, status = [], []
    with sync_playwright() as p:
        browser = p.chromium.launch(
            args=["--disable-blink-features=AutomationControlled"])
        ctx = browser.new_context(user_agent=UA, locale="zh-HK")

        for name, fn in [("康文署 (LCSD)", fetch_lcsd),
                         ("西九 WKProcure(浏览器)", fetch_wkprocure)]:
            try:
                got = fn(ctx)
                tenders.extend(got)
                if got:
                    print(f"  [OK] {name}(浏览器抓取): {len(got)} 条")
                    status.append({"name": name, "ok": True, "count": len(got), "note": ""})
                else:
                    print(f"  [跳过] {name}: 页面无数据或被拦截")
                    status.append({"name": name, "ok": False, "count": 0,
                                   "note": "无数据或被拦截,请手动查看"})
            except Exception as e:
                print(f"  [跳过] {name}: {e.__class__.__name__}")
                status.append({"name": name, "ok": False, "count": 0,
                               "note": str(e.__class__.__name__)})
        browser.close()
    return tenders, status


if __name__ == "__main__":
    ts, st = fetch_all()
    for t in ts[:10]:
        print(f"{t['closing']} | {t['title'][:60]} | {t['ref']}")
    print(f"共 {len(ts)} 条")
