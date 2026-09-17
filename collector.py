#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
香港标书雷达 (HK Tender Radar)
==============================
自动收集香港政府 + 公营机构的招标公告,按「展览 / VR / AR / 多媒体」
相关度打分,生成一个可以双击打开的看板 dashboard.html。

用法(在终端 / 命令行运行):
    python3 collector.py

无需安装任何第三方库,只用 Python 自带模块。
建议每天运行一次(可配合系统定时任务,见 guide.md)。
"""

import json
import os
import re
import ssl
import sys
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, date
from html import unescape

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
os.makedirs(DATA_DIR, exist_ok=True)

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36")

# ---------------------------------------------------------------
# 行业关键词打分(可自定义!)
# 首次运行会在本目录生成 keywords.json,直接编辑该文件即可
# 增删关键词;也可以在看板 dashboard.html 里点「关键词设置」
# 在线编辑(仅影响看板显示)。
# 分数越高越相关;命中任意一个词即标记为「相关」
# ---------------------------------------------------------------
DEFAULT_KEYWORDS = {
    5: ["virtual reality", "augmented reality", "mixed reality", " vr ", " ar ",
        "immersive", "沉浸", "虛擬實境", "虚拟现实", "擴增實境", "增强现实",
        "元宇宙", "metaverse"],
    4: ["exhibition", "展覽", "展览", "展廳", "展厅", "展亭", "pavilion",
        "multimedia", "多媒體", "多媒体", "interactive", "互動", "互动"],
    3: ["museum", "博物館", "博物馆", "gallery", "展館", "美術館",
        "projection", "投影", "audio-visual", "audiovisual", " av ",
        "影音", "視聽", "视听", "digital content", "數碼內容",
        "animation", "動畫", "动画", "video production", "影片製作",
        "creative video", "3d model", "3d 模型"],
    2: ["event production", "活動製作", "活动制作", "stage", "舞台",
        "display", "展示", "signage", "導視", "kiosk", "自助機",
        "lighting design", "燈光設計", "curat", "策展", "設計及製作",
        "design and production", "design, supply", "fabrication",
        "booth", "展位", "visitor centre", "遊客中心", "游客中心",
        "app development", "應用程式", "website", "網站", "网站",
        "touch screen", "觸控", "触控", "led"],
}

KEYWORDS_FILE = os.path.join(BASE_DIR, "keywords.json")
DEFAULT_LABEL = "展览 / VR / AR"


def load_keywords():
    """读取 keywords.json;不存在则用默认值创建它。返回 (关键词dict, 行业标签)"""
    try:
        with open(KEYWORDS_FILE, encoding="utf-8") as f:
            data = json.load(f)
        label = str(data.get("label") or DEFAULT_LABEL)
        kw = {}
        for k, v in data.items():
            if k == "label":
                continue
            if isinstance(v, list):
                kw[int(k)] = [str(w).lower() for w in v if str(w).strip()]
        if kw:
            return kw, label
    except FileNotFoundError:
        pass
    except Exception as e:
        print(f"  [提示] keywords.json 格式有误({e}),本次使用默认关键词")
        return DEFAULT_KEYWORDS, DEFAULT_LABEL
    payload = {"label": DEFAULT_LABEL}
    payload.update({str(k): v for k, v in DEFAULT_KEYWORDS.items()})
    with open(KEYWORDS_FILE, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    print(f"  [提示] 已生成关键词配置文件: {KEYWORDS_FILE} (可直接编辑)")
    return DEFAULT_KEYWORDS, DEFAULT_LABEL


KEYWORDS, INDUSTRY_LABEL = load_keywords()


def score_text(text):
    """返回 (分数, 命中的关键词列表)"""
    t = " " + text.lower() + " "
    total, hits = 0, []
    for pts, words in KEYWORDS.items():
        for w in words:
            if w in t:
                total += pts
                hits.append(w.strip())
    return total, hits


def http_get(url, timeout=25):
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    req = urllib.request.Request(url, headers={"User-Agent": UA,
                                               "Accept-Language": "zh-HK,zh;q=0.9,en;q=0.8"})
    with urllib.request.urlopen(req, timeout=timeout, context=ctx) as r:
        try:
            return r.read()
        except Exception as e:  # 服务器提前断流时保留已读部分
            partial = getattr(e, "partial", None)
            if partial:
                return partial
            raise


def strip_tags(html):
    html = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", html, flags=re.S | re.I)
    html = re.sub(r"<[^>]+>", " ", html)
    return re.sub(r"\s+", " ", unescape(html)).strip()


# ---------------------------------------------------------------
# 数据源 1: 政府物流服务署 电子投标箱(官方开放数据 XML,最稳定)
# 覆盖所有政府部门经中央系统发出的公开招标
# ---------------------------------------------------------------
def fetch_gld():
    out = []
    try:
        raw = http_get("https://pcms2.gld.gov.hk/iportal/TenderNotice.xml")
        root = ET.fromstring(raw)
        for rec in root.findall("TenderNoticeRecord"):
            g = lambda tag: (rec.findtext(tag) or "").strip()
            subject = g("SubjectEnglish")
            subject_c = g("SubjectChinese")
            out.append({
                "source": "政府电子投标箱 (GLD e-Tender Box)",
                "org": g("ReqDepartmentNameChinese") or g("ReqDepartmentNameEnglish"),
                "ref": g("TenderNo"),
                "title": subject_c or subject,
                "title_en": subject,
                "issue_date": g("IssueDateTime")[:10],
                "closing": g("ClosingDateTime")[:16],
                "link": g("Link") or "https://pcms2.gld.gov.hk/iprod",
                "contact": " / ".join(x for x in [g("TelephoneNumber"), g("Email")] if x),
            })
        print(f"  [OK] 政府电子投标箱: {len(out)} 条")
    except Exception as e:
        print(f"  [失败] 政府电子投标箱: {e}")
    return out


# ---------------------------------------------------------------
# 数据源 2: 建筑署 招标公告(官方开放数据 JSON)
# ---------------------------------------------------------------
def fetch_archsd():
    out = []
    try:
        raw = http_get("https://www.archsd.gov.hk/tc/psi/tender.json").decode("utf-8-sig")
        # 官方 JSON 偶尔缺对象间逗号 / 传输中断,做容错修复
        raw = re.sub(r"\}\s*\{", "},{", raw)
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            cut = raw.rfind("},")
            data = json.loads(raw[:cut + 1] + "]") if cut > 0 else []
        items = data if isinstance(data, list) else data.get("tenders", data.get("data", []))
        for it in items if isinstance(items, list) else []:
            if not isinstance(it, dict):
                continue
            title = str(it.get("Subject") or it.get("contractTitle") or it.get("title") or "")
            if not title:
                continue
            closing = str(it.get("Closing date/time") or it.get("closingDate") or "")
            closing = closing.replace("\\/", "/").replace("/", "-")[:16]
            out.append({
                "source": "建筑署 (ArchSD)",
                "org": str(it.get("Procuring Department") or "建筑署"),
                "ref": str(it.get("Reference") or it.get("ref") or ""),
                "title": title,
                "title_en": "",
                "issue_date": "",
                "closing": closing,
                "link": "https://www.archsd.gov.hk/tc/tenders-notices/works/tender-notices-published-in-government-gazette.html",
                "contact": "",
            })
        print(f"  [OK] 建筑署: {len(out)} 条")
    except Exception as e:
        print(f"  [失败] 建筑署: {e}")
    return out


# ---------------------------------------------------------------
# 数据源 3: 西九文化区 WKProcure(M+、香港故宫馆——展览行业重点!)
# 注意: 该站点有防火墙,部分网络环境可能抓不到,失败会自动跳过
# ---------------------------------------------------------------
def fetch_wkcda():
    out = []
    try:
        raw = http_get("https://wkprocure.westkowloon.hk/Guest/en/Tender/List.aspx?Page=1",
                       timeout=20).decode("utf-8", "ignore")
        # 表格行: Ref | Subject | Status | Issue Date | Closing Date
        rows = re.findall(
            r"(WKCDA\d{2}/\d{4}/[A-Z]{3})\s*</[^>]+>.*?<td[^>]*>(.*?)</td>.*?"
            r"(Issued|Closed).*?(\d{4}/\d{2}/\d{2}).*?(\d{4}/\d{2}/\d{2})[^<]*(\d{2}:\d{2})?",
            raw, flags=re.S)
        for ref, subj, status, issued, closing, ctime in rows:
            if status != "Issued":
                continue
            out.append({
                "source": "西九文化区 WKProcure (M+/故宫馆)",
                "org": "西九文化区管理局",
                "ref": ref,
                "title": strip_tags(subj),
                "title_en": "",
                "issue_date": issued.replace("/", "-"),
                "closing": closing.replace("/", "-") + (" " + ctime if ctime else ""),
                "link": "https://wkprocure.westkowloon.hk/Guest/en/Tender/List.aspx",
                "contact": "",
            })
        print(f"  [OK] 西九文化区: {len(out)} 条进行中")
    except Exception as e:
        print(f"  [跳过] 西九文化区(该站有防火墙,可手动查看): {e.__class__.__name__}")
    return out


# ---------------------------------------------------------------
# 数据源 4: 香港科技园 HKSTP(科技/创新类项目)
# ---------------------------------------------------------------
def fetch_hkstp():
    out = []
    try:
        raw = http_get("https://www.hkstp.org/tender-notice/").decode("utf-8", "ignore")
        # 粗提取: 找含 tender 的条目块
        blocks = re.findall(r'<a[^>]+href="([^"]*tender[^"]*)"[^>]*>(.*?)</a>', raw, flags=re.S | re.I)
        seen = set()
        for href, txt in blocks:
            title = strip_tags(txt)
            if len(title) < 15 or title.lower() in seen:
                continue
            seen.add(title.lower())
            link = href if href.startswith("http") else "https://www.hkstp.org" + href
            out.append({
                "source": "香港科技园 (HKSTP)",
                "org": "香港科技园公司",
                "ref": "",
                "title": title,
                "title_en": "",
                "issue_date": "",
                "closing": "",
                "link": link,
                "contact": "",
            })
        print(f"  [OK] 香港科技园: {len(out)} 条")
    except Exception as e:
        print(f"  [跳过] 香港科技园: {e.__class__.__name__}")
    return out


# ---------------------------------------------------------------
# 数据源 5: Google News RSS —— 监控私营企业/上市公司的招标新闻
# 私企发标渠道分散(自家官网、报纸公告、邮件邀请),
# 但大项目往往会见报或发新闻稿,用新闻监控可以捕获一部分
# ---------------------------------------------------------------
NEWS_QUERIES = [
    # (显示名, 查询词)
    ("招标新闻监控", '"招標" OR "投標" 香港 (展覽 OR 博物館 OR 多媒體 OR 體驗館)'),
    ("招标新闻监控", '"invitation to tender" OR "request for proposal" Hong Kong (exhibition OR museum OR immersive OR multimedia)'),
]


def fetch_news_rss():
    out = []
    seen = set()
    for label, q in NEWS_QUERIES:
        try:
            url = ("https://news.google.com/rss/search?q=" +
                   urllib.parse.quote(q) + "&hl=zh-HK&gl=HK&ceid=HK:zh-Hant")
            raw = http_get(url)
            root = ET.fromstring(raw)
            for item in root.iter("item"):
                title = (item.findtext("title") or "").strip()
                link = (item.findtext("link") or "").strip()
                pub = (item.findtext("pubDate") or "").strip()
                if not title or title.lower() in seen:
                    continue
                # 标题必须真的和招标有关,过滤普通新闻
                tl = title.lower()
                if not any(w in tl for w in ("招標", "招标", "投標", "投标", "標書", "标书",
                                             "tender", "rfp", "rfq", "quotation",
                                             "邀請書", "意向書", "eoi")):
                    continue
                seen.add(title.lower())
                # 只保留 90 天内的新闻
                try:
                    from email.utils import parsedate_to_datetime
                    dt = parsedate_to_datetime(pub)
                    if (datetime.now(dt.tzinfo) - dt).days > 90:
                        continue
                    pub_str = dt.strftime("%Y-%m-%d")
                except Exception:
                    pub_str = ""
                out.append({
                    "source": "新闻监控 (Google News)",
                    "org": "",
                    "ref": "",
                    "title": title,
                    "title_en": "",
                    "issue_date": pub_str,
                    "closing": "",
                    "link": link,
                    "contact": "",
                })
        except Exception as e:
            print(f"  [跳过] 新闻监控({label}): {e.__class__.__name__}")
    print(f"  [OK] 新闻监控(私企/上市公司): {len(out)} 条")
    return out


# ---------------------------------------------------------------
# 汇总、打分、去重、排序
# ---------------------------------------------------------------
def collect():
    print("开始收集香港标书信息 ...")
    tenders = []
    for fn in (fetch_gld, fetch_archsd, fetch_wkcda, fetch_hkstp, fetch_news_rss):
        tenders.extend(fn())

    today = date.today()
    seen = set()
    result = []
    for t in tenders:
        key = (t["ref"] or t["title"])[:80]
        if key in seen:
            continue
        seen.add(key)
        s, hits = score_text(t["title"] + " " + t.get("title_en", ""))
        t["score"] = s
        t["hits"] = sorted(set(hits))[:6]
        # 剩余天数
        t["days_left"] = None
        m = re.match(r"(\d{4})-(\d{1,2})-(\d{1,2})", t.get("closing") or "")
        if m:
            try:
                d = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
                t["days_left"] = (d - today).days
            except ValueError:
                pass
        # 已截标的丢弃
        if t["days_left"] is not None and t["days_left"] < 0:
            continue
        result.append(t)

    # 排序: 相关度高优先,其次截标日近的优先
    result.sort(key=lambda x: (-x["score"],
                               x["days_left"] if x["days_left"] is not None else 9999))
    return result


# ---------------------------------------------------------------
# 生成看板 HTML(完全自包含,双击即可打开)
# ---------------------------------------------------------------
def build_dashboard(tenders):
    payload = json.dumps(tenders, ensure_ascii=False)
    kw_json = json.dumps({str(k): v for k, v in KEYWORDS.items()}, ensure_ascii=False)
    updated = datetime.now().strftime("%Y-%m-%d %H:%M")
    n_rel = sum(1 for t in tenders if t["score"] > 0)
    html = DASHBOARD_TEMPLATE.replace("__DATA__", payload) \
                             .replace("__KEYWORDS__", kw_json) \
                             .replace("__LABEL__", INDUSTRY_LABEL) \
                             .replace("__UPDATED__", updated) \
                             .replace("__TOTAL__", str(len(tenders))) \
                             .replace("__REL__", str(n_rel))
    out = os.path.join(BASE_DIR, "dashboard.html")
    with open(out, "w", encoding="utf-8") as f:
        f.write(html)
    # 同时留一份 JSON 存档(带日期,方便回溯)
    with open(os.path.join(DATA_DIR, f"tenders_{date.today().isoformat()}.json"),
              "w", encoding="utf-8") as f:
        f.write(payload)
    return out


DASHBOARD_TEMPLATE = r"""<!DOCTYPE html>
<html lang="zh-HK">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>香港标书雷达</title>
<style>
  :root{--bg:#f4f5f7;--card:#ffffff;--card2:#eef0f3;--txt:#2b2f36;--sub:#6b7280;
        --acc:#2f6fdd;--hot:#d97a1f;--ok:#1f9d5f;--warn:#d64545;--line:#dfe2e8}
  *{box-sizing:border-box;margin:0;padding:0}
  body{font-family:"PingFang SC","Microsoft YaHei",system-ui,sans-serif;
       background:var(--bg);color:var(--txt);padding:24px 16px 60px}
  .wrap{max-width:1080px;margin:0 auto}
  h1{font-size:26px;letter-spacing:1px}
  h1 .radar{color:var(--acc)}
  .meta{color:var(--sub);font-size:13px;margin:6px 0 18px}
  .stats{display:flex;gap:12px;flex-wrap:wrap;margin-bottom:18px}
  .stat{background:var(--card);border:1px solid var(--line);border-radius:12px;
        padding:12px 20px;min-width:130px;box-shadow:0 1px 3px rgba(0,0,0,.05)}
  .stat b{font-size:24px;display:block}
  .stat span{color:var(--sub);font-size:12px}
  .controls{display:flex;gap:10px;flex-wrap:wrap;margin-bottom:16px}
  input[type=search]{flex:1;min-width:220px;background:var(--card);color:var(--txt);
        border:1px solid var(--line);border-radius:10px;padding:10px 14px;font-size:14px;outline:none}
  input[type=search]:focus{border-color:var(--acc)}
  .btn{background:var(--card);color:var(--sub);border:1px solid var(--line);border-radius:10px;
       padding:10px 16px;font-size:13px;cursor:pointer;user-select:none}
  .btn.on{background:var(--acc);color:#ffffff;border-color:var(--acc);font-weight:600}
  .card{background:var(--card);border:1px solid var(--line);border-radius:14px;
        padding:16px 18px;margin-bottom:12px;transition:.15s;box-shadow:0 1px 3px rgba(0,0,0,.05)}
  .card:hover{border-color:var(--acc)}
  .card.hot{border-left:4px solid var(--hot)}
  .row1{display:flex;justify-content:space-between;gap:12px;align-items:flex-start}
  .title{font-size:15.5px;font-weight:600;line-height:1.5}
  .title a{color:var(--txt);text-decoration:none}
  .title a:hover{color:var(--acc)}
  .days{white-space:nowrap;font-size:12.5px;padding:4px 10px;border-radius:999px;font-weight:700}
  .d-ok{background:#e2f5ea;color:var(--ok)}
  .d-soon{background:#faeedd;color:var(--hot)}
  .d-urgent{background:#fbe4e4;color:var(--warn)}
  .d-none{background:#eef0f3;color:var(--sub)}
  .row2{margin-top:8px;display:flex;flex-wrap:wrap;gap:8px;font-size:12.5px;color:var(--sub)}
  .tag{background:var(--card2);border-radius:6px;padding:3px 9px}
  .tag.src{color:var(--acc)}
  .tag.kw{color:var(--hot)}
  .star{cursor:pointer;font-size:18px;line-height:1;background:none;border:none;color:#c3c9d4}
  .star.on{color:#f0b400}
  .kwpanel{display:none;background:var(--card);border:1px solid var(--line);border-radius:14px;
        padding:18px;margin-bottom:16px;box-shadow:0 1px 3px rgba(0,0,0,.05)}
  .kwpanel.open{display:block}
  .kwpanel h3{font-size:15px;margin-bottom:4px}
  .kwpanel .hint{color:var(--sub);font-size:12.5px;margin-bottom:12px;line-height:1.7}
  .kwgroup{margin-bottom:12px}
  .kwgroup label{display:block;font-size:13px;font-weight:600;margin-bottom:5px}
  .kwgroup label em{color:var(--sub);font-weight:400;font-style:normal;font-size:12px}
  .kwgroup textarea{width:100%;min-height:52px;background:var(--card2);color:var(--txt);
        border:1px solid var(--line);border-radius:8px;padding:8px 10px;font-size:13px;
        font-family:inherit;resize:vertical;outline:none;line-height:1.6}
  .kwgroup textarea:focus{border-color:var(--acc)}
  .kwactions{display:flex;gap:10px;flex-wrap:wrap}
  .btn.primary{background:var(--acc);color:#fff;border-color:var(--acc);font-weight:600}
  .btn.danger{color:var(--warn)}
  .empty{color:var(--sub);text-align:center;padding:40px 0}
  .foot{color:var(--sub);font-size:12px;margin-top:24px;line-height:1.8}
  .foot a{color:var(--acc)}
</style>
</head>
<body>
<div class="wrap">
  <h1>香港标书雷达 <span class="radar">◎</span></h1>
  <div class="meta">最后更新:__UPDATED__ · 运行 collector.py 即可刷新数据</div>

  <div class="stats">
    <div class="stat"><b>__TOTAL__</b><span>进行中标书</span></div>
    <div class="stat"><b style="color:var(--hot)">__REL__</b><span id="relLabel">__LABEL__ 相关</span></div>
    <div class="stat"><b id="starCnt">0</b><span>已收藏</span></div>
  </div>

  <div class="controls">
    <input type="search" id="q" placeholder="搜索标题 / 部门 / 编号,如: 展覽、multimedia、博物館 …">
    <div class="btn on" id="fAll">全部</div>
    <div class="btn" id="fRel">只看__LABEL__</div>
    <div class="btn" id="fStar">只看收藏</div>
    <div class="btn" id="fSoon">7天内截标</div>
    <div class="btn" id="kwToggle">⚙ 关键词设置</div>
  </div>

  <div class="kwpanel" id="kwPanel">
    <h3>自定义行业分类与关键词</h3>
    <div class="hint">
      标书标题命中任何关键词即视为「相关」,分数越高排序越靠前,总分≥4分的标书会有橙色高亮边。<br>
      每个关键词用<b>逗号</b>分隔(中英文逗号都行),不分大小写。改完点「保存并应用」立即重新打分排序,设置保存在本浏览器中,下次重新生成看板后依然生效。
    </div>
    <div class="kwgroup"><label>分类名称 <em>(显示在统计卡和筛选按钮上,如:展览 / VR / AR、灯光秀、数字艺术…)</em></label>
      <input type="text" id="kwLabel" style="width:100%;background:var(--card2);color:var(--txt);
        border:1px solid var(--line);border-radius:8px;padding:8px 10px;font-size:13px;
        font-family:inherit;outline:none"></div>
    <div class="kwgroup"><label>5 分 · 核心业务 <em>(最高优先,如 VR/AR/沉浸式)</em></label>
      <textarea id="kw5"></textarea></div>
    <div class="kwgroup"><label>4 分 · 高度相关 <em>(如 展览/多媒体/互动)</em></label>
      <textarea id="kw4"></textarea></div>
    <div class="kwgroup"><label>3 分 · 相关 <em>(如 博物馆/投影/动画)</em></label>
      <textarea id="kw3"></textarea></div>
    <div class="kwgroup"><label>2 分 · 沾边 <em>(如 活动制作/展示/LED)</em></label>
      <textarea id="kw2"></textarea></div>
    <div class="kwactions">
      <div class="btn primary" id="kwSave">保存并应用</div>
      <div class="btn danger" id="kwReset">恢复默认</div>
      <div class="btn" id="kwClose">收起</div>
    </div>
  </div>

  <div id="list"></div>

  <div class="foot">
    自动数据来源:政府物流服务署电子投标箱(官方开放数据)、建筑署、西九文化区 WKProcure、香港科技园、Google News 招标新闻监控。<br><br>
    <b style="color:var(--txt)">公营机构补充源</b>(部分网站有反爬,建议每周手动看一次):
    <a href="https://www.westk.hk/en/tender-notices-expression-interest-0" target="_blank">西九文化区官网</a> ·
    <a href="https://www.lcsd.gov.hk/en/aboutlcsd/tender.html" target="_blank">康文署(博物馆展览大户)</a> ·
    <a href="https://home.hktdc.com/en/s/tender-notices" target="_blank">贸发局 HKTDC(会展大户)</a> ·
    <a href="https://www.cyberport.hk/en/about_cyberport/tender_notice" target="_blank">数码港</a> ·
    <a href="https://www.discoverhongkong.com/eng/hktb/tenders.html" target="_blank">旅发局</a> ·
    <a href="https://www.ha.org.hk" target="_blank">医管局</a><br><br>
    <b style="color:var(--txt)">私营企业 / 上市公司供应商门户</b>(注册一次,之后有标自动收到邀请):
    <a href="https://www.hkextender.com" target="_blank">港铁 MTR(→ 2026/10 起迁往新 Supplier Portal)</a> ·
    <a href="https://suppliernetwork.hkjc.com/" target="_blank">赛马会 HKJC EPRO</a> ·
    <a href="https://tender.hkstp.org" target="_blank">科技园招标系统</a> ·
    <a href="https://eproq.hkpc.org" target="_blank">生产力促进局 ePROQ</a> ·
    <a href="https://www.hongkongairport.com/en/business-partners/tender-notices/" target="_blank">机管局</a> ·
    <a href="https://www.clpgroup.com/en/about/procurement.html" target="_blank">中电 CLP</a> ·
    <a href="https://www.feo.hku.hk/finance/suppliers/supplierportal.html" target="_blank">港大供应商门户</a> ·
    <a href="https://tenderking.hk/" target="_blank">标王 TenderKing(私企/学校/法团发标平台)</a> ·
    <a href="https://brplatform.org.hk/en/e-tendering-platform" target="_blank">市建局 Smart Tender(楼宇工程)</a>
  </div>
</div>

<script>
const DATA = __DATA__;
const DEFAULT_KW = __KEYWORDS__;
const DEFAULT_LABEL = "__LABEL__";
let mode = "all";
const $ = id => document.getElementById(id);
let stars = {};
try { stars = JSON.parse(localStorage.getItem("hkstars")||"{}"); } catch(e){}

// ---------- 自定义关键词 + 分类名称 ----------
let KW = null, LABEL = DEFAULT_LABEL;
try { KW = JSON.parse(localStorage.getItem("hkkw")||"null"); } catch(e){}
try { LABEL = localStorage.getItem("hklabel") || DEFAULT_LABEL; } catch(e){}
if(!KW) KW = DEFAULT_KW;

function applyLabel(){
  $("relLabel").textContent = LABEL + " 相关";
  $("fRel").textContent = "只看" + LABEL;
}

function rescore(){
  // 用当前关键词对所有标书重新打分
  for(const t of DATA){
    const blob = " " + (t.title+" "+(t.title_en||"")).toLowerCase() + " ";
    let score = 0, hits = [];
    for(const pts of Object.keys(KW)){
      for(const w of KW[pts]){
        const kw = w.toLowerCase();
        if(kw && blob.includes(kw)){ score += parseInt(pts); hits.push(kw.trim()); }
      }
    }
    t.score = score;
    t.hits = [...new Set(hits)].slice(0,6);
  }
  DATA.sort((a,b)=> (b.score-a.score) ||
      ((a.days_left==null?9999:a.days_left)-(b.days_left==null?9999:b.days_left)));
  const rel = DATA.filter(t=>t.score>0).length;
  const relEl = document.querySelector(".stats .stat:nth-child(2) b");
  if(relEl) relEl.textContent = rel;
}

function fillPanel(){
  $("kwLabel").value = LABEL;
  for(const p of [5,4,3,2]){
    $("kw"+p).value = (KW[p]||KW[String(p)]||[]).join(", ");
  }
}
function readPanel(){
  const kw = {};
  for(const p of [5,4,3,2]){
    const words = $("kw"+p).value.split(/[,，]/).map(s=>s.trim()).filter(Boolean);
    if(words.length) kw[p] = words;
  }
  return kw;
}

function keyOf(t){ return t.ref || t.title.slice(0,60); }

function daysBadge(t){
  if(t.days_left===null || t.days_left===undefined)
      return '<span class="days d-none">截标日期见公告</span>';
  if(t.days_left<=3) return '<span class="days d-urgent">剩 '+t.days_left+' 天</span>';
  if(t.days_left<=7) return '<span class="days d-soon">剩 '+t.days_left+' 天</span>';
  return '<span class="days d-ok">剩 '+t.days_left+' 天</span>';
}

function render(){
  const q = $("q").value.trim().toLowerCase();
  const list = $("list");
  let shown = 0, html = "";
  for(const t of DATA){
    if(mode==="rel" && t.score<=0) continue;
    if(mode==="star" && !stars[keyOf(t)]) continue;
    if(mode==="soon" && !(t.days_left!==null && t.days_left<=7)) continue;
    const blob = (t.title+" "+(t.title_en||"")+" "+t.org+" "+t.ref+" "+t.source).toLowerCase();
    if(q && !blob.includes(q)) continue;
    shown++;
    const st = stars[keyOf(t)] ? "on":"";
    html += '<div class="card '+(t.score>=4?'hot':'')+'">'
      +'<div class="row1"><div class="title">'
      +'<button class="star '+st+'" data-k="'+encodeURIComponent(keyOf(t))+'">★</button> '
      +'<a href="'+t.link+'" target="_blank">'+t.title+'</a></div>'+daysBadge(t)+'</div>'
      +'<div class="row2">'
      +'<span class="tag src">'+t.source+'</span>'
      +(t.org?'<span class="tag">'+t.org+'</span>':'')
      +(t.ref?'<span class="tag">'+t.ref+'</span>':'')
      +(t.closing?'<span class="tag">截标 '+t.closing+'</span>':'')
      +(t.hits&&t.hits.length?'<span class="tag kw">命中: '+t.hits.join("、")+'</span>':'')
      +(t.contact?'<span class="tag">'+t.contact+'</span>':'')
      +'</div></div>';
  }
  list.innerHTML = html || '<div class="empty">没有符合条件的标书</div>';
  $("starCnt").textContent = Object.keys(stars).filter(k=>stars[k]).length;
  document.querySelectorAll(".star").forEach(b=>{
    b.onclick = e=>{
      const k = decodeURIComponent(b.dataset.k);
      stars[k] = !stars[k];
      try{ localStorage.setItem("hkstars", JSON.stringify(stars)); }catch(e){}
      render();
    };
  });
}

function setMode(m, el){
  mode = m;
  ["fAll","fRel","fStar","fSoon"].forEach(id=>$(id).classList.remove("on"));
  el.classList.add("on");
  render();
}
$("fAll").onclick = e=>setMode("all", e.target);
$("fRel").onclick = e=>setMode("rel", e.target);
$("fStar").onclick = e=>setMode("star", e.target);
$("fSoon").onclick = e=>setMode("soon", e.target);
$("q").oninput = render;

// ---------- 关键词面板事件 ----------
$("kwToggle").onclick = ()=>{
  $("kwPanel").classList.toggle("open");
  fillPanel();
};
$("kwClose").onclick = ()=> $("kwPanel").classList.remove("open");
$("kwSave").onclick = ()=>{
  KW = readPanel();
  LABEL = $("kwLabel").value.trim() || DEFAULT_LABEL;
  try{
    localStorage.setItem("hkkw", JSON.stringify(KW));
    localStorage.setItem("hklabel", LABEL);
  }catch(e){}
  applyLabel(); rescore(); render();
  $("kwSave").textContent = "✓ 已应用";
  setTimeout(()=> $("kwSave").textContent = "保存并应用", 1200);
};
$("kwReset").onclick = ()=>{
  KW = DEFAULT_KW; LABEL = DEFAULT_LABEL;
  try{ localStorage.removeItem("hkkw"); localStorage.removeItem("hklabel"); }catch(e){}
  fillPanel(); applyLabel(); rescore(); render();
};

applyLabel();
rescore();
render();
</script>
</body>
</html>
"""


if __name__ == "__main__":
    tenders = collect()
    out = build_dashboard(tenders)
    rel = [t for t in tenders if t["score"] > 0]
    print(f"\n共收集 {len(tenders)} 条进行中标书,其中 {len(rel)} 条与「{INDUSTRY_LABEL}」相关。")
    print(f"看板已生成: {out}")
    if rel[:5]:
        print("\n最相关的几条:")
        for t in rel[:5]:
            print(f"  · [{t['score']}分] {t['title'][:50]} (截标 {t.get('closing','?')})")
