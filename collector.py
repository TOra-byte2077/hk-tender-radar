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

# 各数据源的抓取状态(显示在看板上,便于发现某个源失效)
SOURCE_STATUS = []


def report(name, ok, count=0, note=""):
    SOURCE_STATUS.append({"name": name, "ok": ok, "count": count, "note": note})

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
        "multimedia", "多媒體", "多媒体", "interactive", "互動", "互动",
        "pop-up", "快閃", "快闪", "brand activation", "experiential marketing",
        "roadshow", "巡展", "路演", "brand experience", "品牌體驗"],
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
PAGE_PASSWORD = ""   # 看板访问密码;留空 = 不设密码。也可在 keywords.json 里加 "password" 字段


def load_keywords():
    """读取 keywords.json;不存在则用默认值创建它。返回 (关键词dict, 行业标签)"""
    global PAGE_PASSWORD
    try:
        with open(KEYWORDS_FILE, encoding="utf-8") as f:
            data = json.load(f)
        label = str(data.get("label") or DEFAULT_LABEL)
        PAGE_PASSWORD = str(data.get("password") or PAGE_PASSWORD or "")
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
        report("政府电子投标箱 (GLD)", True, len(out))
    except Exception as e:
        print(f"  [失败] 政府电子投标箱: {e}")
        report("政府电子投标箱 (GLD)", False, note=str(e)[:60])
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
        report("建筑署 (ArchSD)", True, len(out))
    except Exception as e:
        print(f"  [失败] 建筑署: {e}")
        report("建筑署 (ArchSD)", False, note=str(e)[:60])
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
        report("西九文化区 WKProcure", True, len(out))
    except Exception as e:
        print(f"  [跳过] 西九文化区(该站有防火墙,可手动查看): {e.__class__.__name__}")
        report("西九文化区 WKProcure", False, note="站点防火墙拦截,请手动查看")
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
        report("香港科技园 (HKSTP)", True, len(out))
    except Exception as e:
        print(f"  [跳过] 香港科技园: {e.__class__.__name__}")
        report("香港科技园 (HKSTP)", False, note=str(e.__class__.__name__))
    return out


# ---------------------------------------------------------------
# 数据源 5: 金管局 HKMA 招标公告(官方 API)
# ---------------------------------------------------------------
def fetch_hkma():
    out = []
    try:
        raw = http_get("https://api.hkma.gov.hk/public/tender-invitations"
                       "?lang=tc&segment=tender&pagesize=50")
        data = json.loads(raw)
        for r in (data.get("result") or {}).get("records", []):
            title = str(r.get("title") or r.get("subject") or "").strip()
            if not title:
                continue
            out.append({
                "source": "金管局 (HKMA)",
                "org": "香港金融管理局",
                "ref": str(r.get("ref_no") or ""),
                "title": title,
                "title_en": "",
                "issue_date": str(r.get("issue_date") or "")[:10],
                "closing": str(r.get("closing_date") or "")[:16],
                "link": str(r.get("link") or "https://www.hkma.gov.hk/chi/other-information/tender-invitations/"),
                "contact": "",
            })
        print(f"  [OK] 金管局: {len(out)} 条")
        report("金管局 (HKMA)", True, len(out))
    except Exception as e:
        print(f"  [跳过] 金管局: {e.__class__.__name__}")
        report("金管局 (HKMA)", False, note=str(e.__class__.__name__))
    return out


# ---------------------------------------------------------------
# 数据源 6: 政府新闻公报 RSS(中英双语)
# 部门签约/招标/资格预审的新闻稿常在这里首发,可提前发现机会
# ---------------------------------------------------------------
def fetch_gov_press():
    out = []
    feeds = [
        ("https://www.info.gov.hk/gia/rss/general_zh.xml",
         ("招標", "投標", "標書", "資格預審", "意向書", "採購")),
        ("https://www.info.gov.hk/gia/rss/general_en.xml",
         ("tender", "prequalification", "expression of interest",
          "procurement", "request for proposal")),
    ]
    for url, words in feeds:
        try:
            raw = http_get(url)
            root = ET.fromstring(raw)
            for item in root.iter("item"):
                title = (item.findtext("title") or "").strip()
                link = (item.findtext("link") or "").strip()
                tl = title.lower()
                if not title or not any(w.lower() in tl for w in words):
                    continue
                # 排除金融市场类噪音(外汇基金票据/债券投标结果等)
                if any(w in title for w in ("外匯基金", "外汇基金", "債券", "债券",
                                            "Exchange Fund", "票據", "票据")):
                    continue
                pub = (item.findtext("pubDate") or "").strip()
                try:
                    from email.utils import parsedate_to_datetime
                    pub_str = parsedate_to_datetime(pub).strftime("%Y-%m-%d")
                except Exception:
                    pub_str = ""
                out.append({
                    "source": "政府新闻公报 (ISD)",
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
            print(f"  [跳过] 政府新闻公报: {e.__class__.__name__}")
    # 去重(中英稿可能重复报道同一事件,保留标题不同的)
    print(f"  [OK] 政府新闻公报: {len(out)} 条含招标关键词")
    report("政府新闻公报 (ISD)", True, len(out))
    return out


# ---------------------------------------------------------------
# 数据源 7: Google News RSS —— 监控私营企业/上市公司的招标新闻
# 私企发标渠道分散(自家官网、报纸公告、邮件邀请),
# 但大项目往往会见报或发新闻稿,用新闻监控可以捕获一部分
# ---------------------------------------------------------------
NEWS_QUERIES = [
    # (显示名, 查询词) —— 覆盖面越广越不容易漏,靠标题过滤去噪
    ("招标新闻监控", '"招標" OR "投標" 香港 (展覽 OR 博物館 OR 多媒體 OR 體驗館)'),
    ("招标新闻监控", '"invitation to tender" OR "request for proposal" Hong Kong (exhibition OR museum OR immersive OR multimedia)'),
    ("招标新闻监控", '香港 (招標 OR 標書) (商場 OR 地產 OR 活動 OR 燈光 OR 文化)'),
    ("招标新闻监控", 'Hong Kong tender (mall OR property OR event OR attraction OR "theme park")'),
    ("招标新闻监控", '(西九 OR M+ OR 故宮 OR 康文署 OR 旅發局 OR 貿發局) 招標'),
    ("招标新闻监控", '香港 (快閃 OR "pop-up" OR 品牌體驗) (招標 OR 標書 OR 徵集 OR proposal)'),
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
    report("新闻监控 (Google News)", True, len(out))
    return out


# ---------------------------------------------------------------
# 全球标书源(内嵌模块):世界银行 / 欧盟 TED / 英国 / 加拿大
# 全球数据量巨大,只保留命中行业关键词(keywords.json)的标书
# ---------------------------------------------------------------
GLOBAL_CAP = 40          # 每个全球源最多保留条数
WB_QUERIES = ["museum exhibition", "virtual reality", "audio visual",
              "visitor center interactive", "multimedia exhibition"]
# TED 用 CPV 行业分类码检索(语言无关): 博物馆服务/展览组织/展览设备/
# 展台/视听器材/多媒体设备/艺术服务/多媒体软件开发
TED_CPV = "92521000 79956000 39154000 39154100 32321300 32322000 92312000 72212520"


def _http_post_json(url, payload, timeout=30):
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode(),
        headers={"User-Agent": UA, "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout, context=ctx) as r:
        return json.loads(r.read())


def _gmk(source, org, ref, title, closing, link, issue=""):
    return {"source": source, "org": org, "ref": ref, "title": title,
            "title_en": "", "issue_date": issue, "closing": closing,
            "link": link, "contact": ""}


def fetch_g_worldbank():
    out, seen = [], set()
    for q in WB_QUERIES:
        try:
            url = ("https://search.worldbank.org/api/v2/procnotices?format=json"
                   "&rows=30&notice_status=Published&qterm=" + urllib.parse.quote(q))
            d = json.loads(http_get(url))
            for n in d.get("procnotices", []):
                nid = str(n.get("id") or "")
                if not nid or nid in seen:
                    continue
                seen.add(nid)
                title = str(n.get("bid_description") or n.get("project_name") or "").strip()
                if not title:
                    continue
                s, _ = score_text(title)
                if s <= 0:
                    continue
                deadline = str(n.get("submission_deadline_date") or "")[:10]
                if deadline and deadline < date.today().isoformat():
                    continue
                if not deadline:
                    try:
                        pub = datetime.strptime(str(n.get("noticedate") or ""),
                                                "%d-%b-%Y").date()
                        if (date.today() - pub).days > 120:
                            continue
                    except ValueError:
                        continue
                country = str(n.get("project_ctry_name") or "")
                out.append(_gmk("世界银行 (World Bank)", country,
                                str(n.get("bid_reference_no") or nid),
                                f"[{country}] {title}", deadline,
                                "https://projects.worldbank.org/en/projects-operations/procurement-detail/" + nid))
        except Exception:
            continue
    return out


def fetch_g_ted():
    out = []
    from datetime import timedelta
    since = (date.today() - timedelta(days=45)).strftime("%Y%m%d")
    try:
        d = _http_post_json("https://api.ted.europa.eu/v3/notices/search", {
            "query": f"classification-cpv IN ({TED_CPV}) AND publication-date>={since}",
            "fields": ["notice-title", "publication-number", "buyer-name",
                       "buyer-country", "publication-date",
                       "deadline-receipt-tender-date-lot"],
            "limit": 100})
        for n in d.get("notices", []):
            tt = n.get("notice-title") or {}
            title = tt.get("eng") or (list(tt.values())[0] if tt else "")
            if isinstance(title, list):
                title = title[0] if title else ""
            title = str(title).strip()
            if not title:
                continue
            country = n.get("buyer-country")
            if isinstance(country, list):
                country = country[0] if country else ""
            dl = n.get("deadline-receipt-tender-date-lot")
            if isinstance(dl, list):
                dl = dl[0] if dl else ""
            closing = str(dl or "")[:10]
            if closing and closing < date.today().isoformat():
                continue
            buyer = n.get("buyer-name") or {}
            if isinstance(buyer, dict):
                bv = buyer.get("eng") or (list(buyer.values())[0] if buyer else "")
                buyer = bv[0] if isinstance(bv, list) and bv else bv
            pub = str(n.get("publication-number") or "")
            out.append(_gmk("欧盟 TED", str(buyer or country or ""), pub,
                            f"[{country}] {title}", closing,
                            "https://ted.europa.eu/en/notice/-/detail/" + pub,
                            str(n.get("publication-date") or "")[:10]))
    except Exception:
        pass
    return out


def fetch_g_uk():
    out = []
    try:
        d = json.loads(http_get(
            "https://www.find-tender.service.gov.uk/api/1.0/ocdsReleasePackages?limit=100"))
        for rel in d.get("releases", []):
            tender = rel.get("tender") or {}
            title = str(tender.get("title") or "").strip()
            if not title:
                continue
            s, _ = score_text(title)
            if s <= 0:
                continue
            status = str(tender.get("status") or "").lower()
            if status in ("complete", "cancelled", "unsuccessful", "withdrawn"):
                continue
            closing = str((tender.get("tenderPeriod") or {}).get("endDate") or "")[:10]
            if closing and closing < date.today().isoformat():
                continue
            buyer = str((rel.get("buyer") or {}).get("name") or "")
            out.append(_gmk("英国 Find a Tender", buyer, str(rel.get("ocid") or ""),
                            f"[UK] {title}", closing,
                            "https://www.find-tender.service.gov.uk/Search/Results?Keywords=" +
                            urllib.parse.quote(title[:60])))
    except Exception:
        pass
    return out


def fetch_g_canada():
    out = []
    try:
        import csv as _csv
        import io
        raw = http_get("https://canadabuys.canada.ca/opendata/pub/"
                       "openTenderNotice-ouvertAvisAppelOffres.csv", timeout=60)
        rd = _csv.DictReader(io.StringIO(raw.decode("utf-8-sig", "ignore")))
        for row in rd:
            title = str(row.get("title-titre-eng") or "").strip()
            if not title:
                continue
            s, _ = score_text(title)
            if s <= 0:
                continue
            if str(row.get("tenderStatus-appelOffresStatut-eng") or "").lower() != "open":
                continue
            closing = str(row.get("tenderClosingDate-appelOffresDateCloture") or "")[:10]
            if closing and closing < date.today().isoformat():
                continue
            out.append(_gmk("加拿大 CanadaBuys", "加拿大政府",
                            str(row.get("referenceNumber-numeroReference") or ""),
                            f"[CA] {title}", closing,
                            str(row.get("noticeURL-URLavis-eng") or
                                "https://canadabuys.canada.ca/en/tender-opportunities")))
    except Exception:
        pass
    return out


def fetch_global_all():
    tenders = []
    for name, fn in [("世界银行 (World Bank)", fetch_g_worldbank),
                     ("欧盟 TED", fetch_g_ted),
                     ("英国 Find a Tender", fetch_g_uk),
                     ("加拿大 CanadaBuys", fetch_g_canada)]:
        try:
            got = fn()
            got.sort(key=lambda t: t.get("closing") or "9999")
            got = got[:GLOBAL_CAP]
            tenders.extend(got)
            print(f"  [OK] {name}: {len(got)} 条(行业相关)")
            report(name, True, len(got))
        except Exception as e:
            print(f"  [跳过] {name}: {e.__class__.__name__}")
            report(name, False, note=str(e.__class__.__name__))
    return tenders


# ---------------------------------------------------------------
# 汇总、打分、去重、排序
# ---------------------------------------------------------------
def collect():
    print("开始收集香港标书信息 ...")
    tenders = []
    for fn in (fetch_gld, fetch_archsd, fetch_wkcda, fetch_hkstp,
               fetch_hkma, fetch_gov_press, fetch_news_rss):
        tenders.extend(fn())

    # 全球标书源(世界银行/欧盟TED/英国/加拿大,只保留行业相关)
    # 代码已内嵌在本文件(见 fetch_global_all),无需单独的 global_sources.py
    tenders.extend(fetch_global_all())

    # 可选:浏览器自动化源(康文署等动态/反爬网站)
    # 本地没装 Playwright 会自动跳过;GitHub Actions 上自动启用
    try:
        import browser_sources
        bt, bs = browser_sources.fetch_all()
        tenders.extend(bt)
        SOURCE_STATUS.extend(bs)
    except ImportError:
        print("  [提示] 未安装 Playwright,跳过浏览器抓取源(康文署等)。"
              "安装方法见 browser_sources.py 顶部说明")
    except Exception as e:
        print(f"  [跳过] 浏览器抓取源: {e.__class__.__name__}")
        report("浏览器抓取源", False, note=str(e.__class__.__name__)[:40])

    # 用香港日期计算剩余天数(避免云端 UTC 时区导致差一天)
    try:
        from zoneinfo import ZoneInfo
        today = datetime.now(ZoneInfo("Asia/Hong_Kong")).date()
    except Exception:
        from datetime import timezone, timedelta
        today = datetime.now(timezone(timedelta(hours=8))).date()
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
    status_json = json.dumps(SOURCE_STATUS, ensure_ascii=False)
    kw_json = json.dumps({str(k): v for k, v in KEYWORDS.items()}, ensure_ascii=False)
    # 密码只嵌入 SHA-256 哈希,不嵌明文
    import hashlib
    pw_hash = (hashlib.sha256(PAGE_PASSWORD.encode("utf-8")).hexdigest()
               if PAGE_PASSWORD else "")
    # 统一用香港时间显示(无论在哪台机器/哪个时区运行)
    try:
        from zoneinfo import ZoneInfo
        now_hk = datetime.now(ZoneInfo("Asia/Hong_Kong"))
    except Exception:
        from datetime import timezone, timedelta
        now_hk = datetime.now(timezone(timedelta(hours=8)))
    updated = now_hk.strftime("%Y-%m-%d %H:%M") + "(香港时间)"
    n_rel = sum(1 for t in tenders if t["score"] > 0)
    html = DASHBOARD_TEMPLATE.replace("__DATA__", payload) \
                             .replace("__KEYWORDS__", kw_json) \
                             .replace("__STATUS__", status_json) \
                             .replace("__LABEL__", INDUSTRY_LABEL) \
                             .replace("__UPDATED__", updated) \
                             .replace("__TOTAL__", str(len(tenders))) \
                             .replace("__REL__", str(n_rel)) \
                             .replace("__PWHASH__", pw_hash)
    out = os.path.join(BASE_DIR, "dashboard.html")
    with open(out, "w", encoding="utf-8") as f:
        f.write(html)
    # 同时留一份 JSON 存档(带日期,方便回溯)
    with open(os.path.join(DATA_DIR, f"tenders_{now_hk.date().isoformat()}.json"),
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
  /* ---- 密码锁(日记簿式) ---- */
  #lock{position:fixed;inset:0;background:var(--bg);z-index:9999;
        display:flex;align-items:center;justify-content:center}
  .lockbox{background:var(--card);border:1px solid var(--line);border-radius:16px;
        padding:36px 40px;text-align:center;box-shadow:0 8px 30px rgba(0,0,0,.12);
        max-width:340px;width:90%}
  .lockbox .icon{font-size:40px;margin-bottom:10px}
  .lockbox h2{font-size:18px;margin-bottom:6px}
  .lockbox p{color:var(--sub);font-size:13px;margin-bottom:18px}
  .lockbox input{width:100%;text-align:center;letter-spacing:4px;font-size:18px;
        background:var(--card2);color:var(--txt);border:1px solid var(--line);
        border-radius:10px;padding:12px;outline:none;margin-bottom:14px}
  .lockbox input:focus{border-color:var(--acc)}
  .lockbox .go{width:100%;background:var(--acc);color:#fff;border:none;border-radius:10px;
        padding:12px;font-size:15px;font-weight:600;cursor:pointer}
  .lockbox .err{color:var(--warn);font-size:12.5px;height:18px;margin-top:10px}
  .lockbox.shake{animation:shake .4s}
  @keyframes shake{0%,100%{transform:translateX(0)}20%,60%{transform:translateX(-8px)}
        40%,80%{transform:translateX(8px)}}
  .empty{color:var(--sub);text-align:center;padding:40px 0}
  .foot{color:var(--sub);font-size:12px;margin-top:24px;line-height:1.8}
  .foot a{color:var(--acc)}
</style>
</head>
<body>
<div id="lock" style="display:none">
  <div class="lockbox" id="lockbox">
    <div class="icon">🔒</div>
    <h2>標書雷達 Tender Radar</h2>
    <p>此看板已加鎖,請輸入訪問密碼<br>This dashboard is locked. Enter password.</p>
    <input type="password" id="pwInput" placeholder="········" autocomplete="off">
    <button class="go" id="pwGo">解鎖 Unlock</button>
    <div class="err" id="pwErr"></div>
  </div>
</div>
<div class="wrap">
  <h1><span id="hTitle">標書雷達</span> <span class="radar">◎</span>
      <span style="float:right"><span class="btn" id="langToggle" style="font-size:13px;padding:7px 14px">EN</span></span></h1>
  <div class="meta"><span id="mUpdated">最後更新</span>:__UPDATED__ · <span id="mHint">運行 collector.py 即可刷新數據</span> · <span id="srcStatus"></span></div>

  <div class="stats">
    <div class="stat"><b>__TOTAL__</b><span id="statTotal">進行中標書</span></div>
    <div class="stat"><b style="color:var(--hot)">__REL__</b><span id="relLabel">__LABEL__ 相關</span></div>
    <div class="stat"><b id="starCnt">0</b><span id="statStar">已收藏</span></div>
  </div>

  <div class="controls">
    <input type="search" id="q" placeholder="搜尋標題 / 部門 / 編號,如: 展覽、multimedia、博物館 …">
    <div class="btn on" id="fAll">全部</div>
    <div class="btn" id="fRel">只看__LABEL__</div>
    <div class="btn" id="fStar">只看收藏</div>
    <div class="btn" id="fSoon">7天內截標</div>
    <div class="btn" id="kwToggle">⚙ 關鍵詞設置</div>
  </div>

  <div class="kwpanel" id="kwPanel">
    <h3 id="kpTitle">自定義行業分類與關鍵詞</h3>
    <div class="hint" id="kpHint">
      標書標題命中任何關鍵詞即視為「相關」,分數越高排序越靠前,總分≥4分的標書會有橙色高亮邊。<br>
      每個關鍵詞用<b>逗號</b>分隔(中英文逗號都行),不分大小寫。改完點「保存並應用」立即重新打分排序,設置保存在本瀏覽器中,下次重新生成看板後依然生效。
    </div>
    <div class="kwgroup"><label id="lbLabel">分類名稱 <em>(顯示在統計卡和篩選按鈕上)</em></label>
      <input type="text" id="kwLabel" style="width:100%;background:var(--card2);color:var(--txt);
        border:1px solid var(--line);border-radius:8px;padding:8px 10px;font-size:13px;
        font-family:inherit;outline:none"></div>
    <div class="kwgroup"><label id="lb5">5 分 · 核心業務 <em>(如 VR/AR/沉浸式)</em></label>
      <textarea id="kw5"></textarea></div>
    <div class="kwgroup"><label id="lb4">4 分 · 高度相關 <em>(如 展覽/快閃/多媒體)</em></label>
      <textarea id="kw4"></textarea></div>
    <div class="kwgroup"><label id="lb3">3 分 · 相關 <em>(如 博物館/投影/動畫)</em></label>
      <textarea id="kw3"></textarea></div>
    <div class="kwgroup"><label id="lb2">2 分 · 沾邊 <em>(如 活動製作/展示/LED)</em></label>
      <textarea id="kw2"></textarea></div>
    <div class="kwactions">
      <div class="btn primary" id="kwSave">保存並應用</div>
      <div class="btn danger" id="kwReset">恢復默認</div>
      <div class="btn" id="kwClose">收起</div>
    </div>
  </div>

  <div id="list"></div>

  <div class="foot" id="footZh">
    自動數據來源:政府電子投標箱、建築署、西九 WKProcure、科技園、金管局、政府新聞公報、Google News 監控、康文署(瀏覽器)、世界銀行、歐盟 TED、英國 Find a Tender、加拿大 CanadaBuys。<br><br>
    <b style="color:var(--txt)">公營機構補充源</b>(部分網站有反爬,建議每週手動看一次):
    <a href="https://www.westk.hk/en/tender-notices-expression-interest-0" target="_blank">西九文化区官网</a> ·
    <a href="https://www.lcsd.gov.hk/clpss/tc/webApp/Tender.do" target="_blank">康文署招标/报价列表(博物馆展览大户)</a> ·
    <a href="https://www.lcsd.gov.hk/clpss/tc/webApp/NoticeList.do" target="_blank">康文署通知名单(加入报价邀请!)</a> ·
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
    <a href="https://brplatform.org.hk/en/e-tendering-platform" target="_blank">市建局 Smart Tender(楼宇工程)</a><br><br>
    <b style="color:var(--txt)">全球标书</b>(已自动抓取:世界银行、欧盟TED、英国、加拿大;以下需注册或手动查看):
    <a href="https://sam.gov/search/?index=opp" target="_blank">美国 SAM.gov(免费注册)</a> ·
    <a href="https://www.ungm.org/Public/Notice" target="_blank">联合国 UNGM</a> ·
    <a href="https://devbusiness.un.org/" target="_blank">UN Development Business</a> ·
    <a href="https://www.gebiz.gov.sg/" target="_blank">新加坡 GeBIZ</a> ·
    <a href="https://www.tenders.gov.au/atm" target="_blank">澳洲 AusTender</a> ·
    <a href="https://www.marches-publics.gouv.fr/" target="_blank">法国 PLACE</a> ·
    <a href="https://www.evergabe-online.de/" target="_blank">德国 e-Vergabe</a> ·
    <a href="https://www.dubaicustoms.gov.ae/en/Tenders" target="_blank">迪拜/中东各政府门户</a> ·
    <a href="https://ted.europa.eu/en/" target="_blank">欧盟 TED 官网(手动搜更多)</a>
  </div>

  <div class="foot" id="footEn" style="display:none">
    Automated sources: GLD e-Tender Box, ArchSD, WKCDA WKProcure, HKSTP, HKMA, ISD Press Releases, Google News monitoring, LCSD (browser), World Bank, EU TED, UK Find a Tender, CanadaBuys.<br><br>
    <b style="color:var(--txt)">HK public bodies</b> (check weekly, some sites block robots):
    <a href="https://www.westk.hk/en/tender-notices-expression-interest-0" target="_blank">WKCDA</a> ·
    <a href="https://www.lcsd.gov.hk/en/aboutlcsd/services/tender.html" target="_blank">LCSD tender list</a> ·
    <a href="https://home.hktdc.com/en/s/tender-notices" target="_blank">HKTDC</a> ·
    <a href="https://www.cyberport.hk/en/about_cyberport/tender_notice" target="_blank">Cyberport</a> ·
    <a href="https://www.discoverhongkong.com/eng/hktb/tenders.html" target="_blank">HKTB</a> ·
    <a href="https://www.ha.org.hk" target="_blank">Hospital Authority</a><br><br>
    <b style="color:var(--txt)">Supplier portals</b> (register once, invitations come to you):
    <a href="https://www.hkextender.com" target="_blank">MTR</a> ·
    <a href="https://suppliernetwork.hkjc.com/" target="_blank">HKJC EPRO</a> ·
    <a href="https://tender.hkstp.org" target="_blank">HKSTP</a> ·
    <a href="https://eproq.hkpc.org" target="_blank">HKPC ePROQ</a> ·
    <a href="https://www.hongkongairport.com/en/business-partners/tender-notices/" target="_blank">Airport Authority</a> ·
    <a href="https://www.clpgroup.com/en/about/procurement.html" target="_blank">CLP</a> ·
    <a href="https://www.feo.hku.hk/finance/suppliers/supplierportal.html" target="_blank">HKU</a> ·
    <a href="https://tenderking.hk/" target="_blank">TenderKing</a> ·
    <a href="https://brplatform.org.hk/en/e-tendering-platform" target="_blank">URA Smart Tender</a><br><br>
    <b style="color:var(--txt)">Global</b> (auto-fetched: World Bank, EU TED, UK, Canada; below need registration / manual check):
    <a href="https://sam.gov/search/?index=opp" target="_blank">US SAM.gov</a> ·
    <a href="https://www.ungm.org/Public/Notice" target="_blank">UNGM</a> ·
    <a href="https://devbusiness.un.org/" target="_blank">UN Development Business</a> ·
    <a href="https://www.gebiz.gov.sg/" target="_blank">Singapore GeBIZ</a> ·
    <a href="https://www.tenders.gov.au/atm" target="_blank">AusTender</a> ·
    <a href="https://www.marches-publics.gouv.fr/" target="_blank">France PLACE</a> ·
    <a href="https://www.evergabe-online.de/" target="_blank">Germany e-Vergabe</a> ·
    <a href="https://ted.europa.eu/en/" target="_blank">EU TED website</a>
  </div>
</div>

<script>
const DATA = __DATA__;
const DEFAULT_KW = __KEYWORDS__;
const SRC_STATUS = __STATUS__;
const DEFAULT_LABEL = "__LABEL__";
const PW_HASH = "__PWHASH__";

// ---------- 密码锁(日记簿式) ----------
(function(){
  if(!PW_HASH) return;                       // 未设密码,直接进
  let saved = "";
  try { saved = sessionStorage.getItem("hkpw") || ""; } catch(e){}
  if(saved === PW_HASH) return;              // 本次浏览已解锁过
  const lock = document.getElementById("lock");
  lock.style.display = "flex";
  document.querySelector(".wrap").style.display = "none";

  async function sha256(s){
    const buf = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(s));
    return [...new Uint8Array(buf)].map(b=>b.toString(16).padStart(2,"0")).join("");
  }
  async function tryUnlock(){
    const v = document.getElementById("pwInput").value;
    if(!v) return;
    let h = "";
    try { h = await sha256(v); }
    catch(e){  // 极旧浏览器或非 https 环境无 SubtleCrypto,提示改用 https
      document.getElementById("pwErr").textContent = "瀏覽器不支持,請用 https 鏈接打開 / Browser unsupported, use https";
      return;
    }
    if(h === PW_HASH){
      try { sessionStorage.setItem("hkpw", h); } catch(e){}
      lock.style.display = "none";
      document.querySelector(".wrap").style.display = "";
    } else {
      const box = document.getElementById("lockbox");
      document.getElementById("pwErr").textContent = "密碼不對,再試一次 / Wrong password, try again";
      document.getElementById("pwInput").value = "";
      box.classList.remove("shake"); void box.offsetWidth; box.classList.add("shake");
    }
  }
  document.getElementById("pwGo").onclick = tryUnlock;
  document.getElementById("pwInput").addEventListener("keydown",
      e=>{ if(e.key==="Enter") tryUnlock(); });
  setTimeout(()=>document.getElementById("pwInput").focus(), 100);
})();

// 数据源状态摘要由 updateSrcStatus() 按当前语言渲染(见下方 i18n 部分)
let mode = "all";
const $ = id => document.getElementById(id);
let stars = {};
try { stars = JSON.parse(localStorage.getItem("hkstars")||"{}"); } catch(e){}

// ---------- 自定义关键词 + 分类名称 ----------
let KW = null, LABEL = DEFAULT_LABEL;
try { KW = JSON.parse(localStorage.getItem("hkkw")||"null"); } catch(e){}
try { LABEL = localStorage.getItem("hklabel") || DEFAULT_LABEL; } catch(e){}
if(!KW) KW = DEFAULT_KW;

// ---------- 双语 i18n(繁體中文 / English) ----------
let LANG = "zh";
try { LANG = localStorage.getItem("hklang") || "zh"; } catch(e){}

const I18N = {
  zh: {
    hTitle:"標書雷達", mUpdated:"最後更新", mHint:"運行 collector.py 即可刷新數據",
    statTotal:"進行中標書", statStar:"已收藏",
    rel: l => l + " 相關", fRelTxt: l => "只看" + l,
    qPh:"搜尋標題 / 部門 / 編號,如: 展覽、multimedia、博物館 …",
    fAll:"全部", fStar:"只看收藏", fSoon:"7天內截標", kwToggle:"⚙ 關鍵詞設置",
    kpTitle:"自定義行業分類與關鍵詞",
    kpHint:"標書標題命中任何關鍵詞即視為「相關」,分數越高排序越靠前,總分≥4分的標書會有橙色高亮邊。<br>每個關鍵詞用<b>逗號</b>分隔(中英文逗號都行),不分大小寫。改完點「保存並應用」立即重新打分排序,設置保存在本瀏覽器中,下次重新生成看板後依然生效。",
    lbLabel:"分類名稱 <em>(顯示在統計卡和篩選按鈕上)</em>",
    lb5:"5 分 · 核心業務 <em>(如 VR/AR/沉浸式)</em>",
    lb4:"4 分 · 高度相關 <em>(如 展覽/快閃/多媒體)</em>",
    lb3:"3 分 · 相關 <em>(如 博物館/投影/動畫)</em>",
    lb2:"2 分 · 沾邊 <em>(如 活動製作/展示/LED)</em>",
    kwSave:"保存並應用", kwSaved:"✓ 已應用", kwReset:"恢復默認", kwClose:"收起",
    daysLeft: d => "剩 " + d + " 天", noDeadline:"截標日期見公告",
    closes:"截標 ", hits:"命中: ", empty:"沒有符合條件的標書",
    srcOk:"全部數據源正常: ", srcBad: names => "⚠ " + names + " 本次抓取失敗,請到看板底部手動查看",
    srcNormal:" · 正常源: ", langBtn:"EN"
  },
  en: {
    hTitle:"Tender Radar", mUpdated:"Last updated", mHint:"run collector.py to refresh",
    statTotal:"open tenders", statStar:"starred",
    rel: l => l + " related", fRelTxt: l => l + " only",
    qPh:"Search title / buyer / ref, e.g. exhibition, multimedia, museum …",
    fAll:"All", fStar:"Starred", fSoon:"Closing ≤7 days", kwToggle:"⚙ Keywords",
    kpTitle:"Customise category & keywords",
    kpHint:"A tender is \"related\" when its title matches any keyword; higher total score ranks higher, and a score ≥4 gets an orange highlight.<br>Separate keywords with <b>commas</b>, case-insensitive. Click \"Save & apply\" to re-score instantly. Settings are stored in this browser and survive dashboard refreshes.",
    lbLabel:"Category name <em>(shown on the stat card & filter button)</em>",
    lb5:"5 pts · Core business <em>(e.g. VR/AR/immersive)</em>",
    lb4:"4 pts · Highly related <em>(e.g. exhibition/pop-up/multimedia)</em>",
    lb3:"3 pts · Related <em>(e.g. museum/projection/animation)</em>",
    lb2:"2 pts · Peripheral <em>(e.g. event production/display/LED)</em>",
    kwSave:"Save & apply", kwSaved:"✓ Applied", kwReset:"Reset", kwClose:"Close",
    daysLeft: d => d + (d===1?" day left":" days left"), noDeadline:"See notice",
    closes:"Closes ", hits:"Hits: ", empty:"No tenders match",
    srcOk:"All sources OK: ", srcBad: names => "⚠ " + names + " failed this run — check manually via links below",
    srcNormal:" · OK: ", langBtn:"繁"
  }
};
function L(){ return I18N[LANG]; }

function updateSrcStatus(){
  const el = $("srcStatus");
  if(!el || !SRC_STATUS.length) return;
  const bad = SRC_STATUS.filter(s=>!s.ok);
  const okTxt = SRC_STATUS.filter(s=>s.ok).map(s=>s.name+"("+s.count+")").join(" · ");
  if(bad.length){
    el.innerHTML = '<span style="color:#d64545">'+L().srcBad(bad.map(s=>s.name).join("、"))
      +'</span>'+L().srcNormal+okTxt;
  } else {
    el.textContent = L().srcOk + okTxt;
  }
}

function applyLang(){
  const t = L();
  $("hTitle").textContent = t.hTitle;
  $("mUpdated").textContent = t.mUpdated;
  $("mHint").textContent = t.mHint;
  $("statTotal").textContent = t.statTotal;
  $("statStar").textContent = t.statStar;
  $("q").placeholder = t.qPh;
  $("fAll").textContent = t.fAll;
  $("fStar").textContent = t.fStar;
  $("fSoon").textContent = t.fSoon;
  $("kwToggle").textContent = t.kwToggle;
  $("kpTitle").textContent = t.kpTitle;
  $("kpHint").innerHTML = t.kpHint;
  $("lbLabel").innerHTML = t.lbLabel;
  $("lb5").innerHTML = t.lb5;
  $("lb4").innerHTML = t.lb4;
  $("lb3").innerHTML = t.lb3;
  $("lb2").innerHTML = t.lb2;
  $("kwSave").textContent = t.kwSave;
  $("kwReset").textContent = t.kwReset;
  $("kwClose").textContent = t.kwClose;
  $("langToggle").textContent = t.langBtn;
  $("footZh").style.display = (LANG==="zh") ? "" : "none";
  $("footEn").style.display = (LANG==="en") ? "" : "none";
  applyLabel();
  updateSrcStatus();
}

function applyLabel(){
  $("relLabel").textContent = L().rel(LABEL);
  $("fRel").textContent = L().fRelTxt(LABEL);
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
      return '<span class="days d-none">'+L().noDeadline+'</span>';
  const txt = L().daysLeft(t.days_left);
  if(t.days_left<=3) return '<span class="days d-urgent">'+txt+'</span>';
  if(t.days_left<=7) return '<span class="days d-soon">'+txt+'</span>';
  return '<span class="days d-ok">'+txt+'</span>';
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
    // 英文模式优先显示英文标题(政府投标箱等源自带双语)
    const shownTitle = (LANG==="en" && t.title_en) ? t.title_en : t.title;
    html += '<div class="card '+(t.score>=4?'hot':'')+'">'
      +'<div class="row1"><div class="title">'
      +'<button class="star '+st+'" data-k="'+encodeURIComponent(keyOf(t))+'">★</button> '
      +'<a href="'+t.link+'" target="_blank">'+shownTitle+'</a></div>'+daysBadge(t)+'</div>'
      +'<div class="row2">'
      +'<span class="tag src">'+t.source+'</span>'
      +(t.org?'<span class="tag">'+t.org+'</span>':'')
      +(t.ref?'<span class="tag">'+t.ref+'</span>':'')
      +(t.closing?'<span class="tag">'+L().closes+t.closing+'</span>':'')
      +(t.hits&&t.hits.length?'<span class="tag kw">'+L().hits+t.hits.join("、")+'</span>':'')
      +(t.contact?'<span class="tag">'+t.contact+'</span>':'')
      +'</div></div>';
  }
  list.innerHTML = html || '<div class="empty">'+L().empty+'</div>';
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
  $("kwSave").textContent = L().kwSaved;
  setTimeout(()=> $("kwSave").textContent = L().kwSave, 1200);
};
$("kwReset").onclick = ()=>{
  KW = DEFAULT_KW; LABEL = DEFAULT_LABEL;
  try{ localStorage.removeItem("hkkw"); localStorage.removeItem("hklabel"); }catch(e){}
  fillPanel(); applyLabel(); rescore(); render();
};

// ---------- 语言切换 ----------
$("langToggle").onclick = ()=>{
  LANG = (LANG==="zh") ? "en" : "zh";
  try{ localStorage.setItem("hklang", LANG); }catch(e){}
  applyLang(); render();
};

applyLang();
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
