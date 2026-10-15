#!/usr/bin/env python3
"""中美宏观事件抓取 — 运行于 GitHub Actions，无本地依赖。

数据源：东方财富数据中心（官方数据镜像，海外可访问）
  - 中国 CPI / PPI / LPR
  - 美国 CPI（同比）/ PPI（环比）/ 美联储联邦基金利率目标

逻辑：拉取最新已发布读数，与 data/state.json 中基线比对；
  - 月度数据（CPI/PPI）：出现新的报告期 → 生成事件
  - 利率类（LPR/联邦基金）：读数发生变化 → 生成事件
事件追加到 data/events.json（按事件 id 去重），快照写入 data/latest.json。
"""
import json
import os
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

CN_TZ = timezone(timedelta(hours=8))
API = "https://datacenter-web.eastmoney.com/api/data/v1/get"
TOKEN = "894050c76af8597a853f5b408b759f5d"  # 东方财富数据中心公开 token
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
STATE_FILE = os.path.join(DATA_DIR, "state.json")
EVENTS_FILE = os.path.join(DATA_DIR, "events.json")
LATEST_FILE = os.path.join(DATA_DIR, "latest.json")

CN_SOURCES = {
    "cn_cpi": "https://data.eastmoney.com/cjsj/cpi.html",
    "cn_ppi": "https://data.eastmoney.com/cjsj/ppi.html",
    "lpr": "https://data.eastmoney.com/cjsj/globalRateLPR.html",
}
US_SOURCE = "https://data.eastmoney.com/cjsj/foreign_{}.html"


def em_params(**extra):
    p = {
        "reportName": "RPT_ECONOMICVALUE_USA",
        "columns": "ALL",
        "sortColumns": "REPORT_DATE",
        "sortTypes": "-1",
        "source": "WEB",
        "client": "WEB",
        "pageNumber": 1,
        "pageSize": 6,
    }
    p.update(extra)
    return p


def get_json(params):
    url = API + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp)


def latest_published(rows):
    """行按报告期倒序，取第一条已发布（VALUE 非空）的行。"""
    for row in rows:
        if row.get("VALUE") is not None:
            return row
    return None


def month_of(date_str):
    return int(date_str[5:7])


def date_of(date_str):
    return date_str[:10]


def direction_word(value):
    if value > 0:
        return "上涨"
    if value < 0:
        return "下降"
    return "持平"


# ---------- 各指标抓取与事件构造 ----------

def fetch_cn_cpi():
    params = {
        "reportName": "RPT_ECONOMY_CPI", "columns": "ALL",
        "sortColumns": "TIME", "sortTypes": "-1",
        "pageNumber": 1, "pageSize": 4,
    }
    rows = get_json(params)["result"]["data"]
    row = rows[0]
    yoy = row["NATIONAL_SAME"]
    mom = row["NATIONAL_SEQUENTIAL"]
    prev = rows[1]["NATIONAL_SAME"] if len(rows) > 1 else None
    return {
        "report_key": row["REPORT_DATE"][:10],
        "publish_date": None,  # 东财该报表无发布日，用报告期次月 9 日近似
        "value": yoy,
        "title": "中国 {}月 CPI 同比{}{:.1f}%".format(
            month_of(row["REPORT_DATE"]), direction_word(yoy), abs(yoy)),
        "detail": "环比 {:+.1f}%{}".format(mom, "，前值同比 {:.1f}%".format(prev) if prev is not None else ""),
        "source": CN_SOURCES["cn_cpi"],
    }


def fetch_cn_ppi():
    params = {
        "reportName": "RPT_ECONOMY_PPI", "columns": "ALL",
        "sortColumns": "TIME", "sortTypes": "-1",
        "pageNumber": 1, "pageSize": 4,
    }
    rows = get_json(params)["result"]["data"]
    row = rows[0]
    yoy = row["BASE_SAME"]
    prev = rows[1]["BASE_SAME"] if len(rows) > 1 else None
    return {
        "report_key": row["REPORT_DATE"][:10],
        "publish_date": None,
        "value": yoy,
        "title": "中国 {}月 PPI 同比{}{:.1f}%".format(
            month_of(row["REPORT_DATE"]), direction_word(yoy), abs(yoy)),
        "detail": "前值同比 {:.1f}%".format(prev) if prev is not None else "",
        "source": CN_SOURCES["cn_ppi"],
    }


def fetch_lpr():
    params = {
        "reportName": "RPTA_WEB_RATE", "columns": "ALL",
        "sortColumns": "TRADE_DATE", "sortTypes": "-1",
        "token": TOKEN, "pageNumber": 1, "pageSize": 6,
        "p": 1, "pageNo": 1, "pageNum": 1,
    }
    rows = get_json(params)["result"]["data"]
    row, prev_row = rows[0], rows[1]
    cur = (row["LPR1Y"], row["LPR5Y"])
    prev = (prev_row["LPR1Y"], prev_row["LPR5Y"])
    changed = cur != prev
    detail = ""
    if changed:
        d1 = round((cur[0] - prev[0]) * 100)
        d5 = round((cur[1] - prev[1]) * 100)
        detail = "较上次：1年期 {:+}bp，5年期 {:+}bp（前值 {:.1f}% / {:.1f}%）".format(d1, d5, prev[0], prev[1])
    return {
        "report_key": row["TRADE_DATE"][:10],
        "publish_date": row["TRADE_DATE"][:10],
        "value": cur,
        "changed": changed,
        "title": "LPR 报价：1年期 {:.1f}%、5年期 {:.1f}%".format(cur[0], cur[1]),
        "detail": detail,
        "source": CN_SOURCES["lpr"],
    }


def fetch_us_indicator(rid, page_no, value_desc):
    rows = get_json(em_params(filter='(INDICATOR_ID="{}")'.format(rid)))["result"]["data"]
    row = latest_published(rows)
    prev = row.get("PRE_VALUE")
    y, m = row["REPORT_DATE"][:4], month_of(row["REPORT_DATE"])
    v = row["VALUE"]
    return {
        "report_key": row["REPORT_DATE"][:10],
        "publish_date": date_of(row["PUBLISH_DATE"]) if row.get("PUBLISH_DATE") else None,
        "value": v,
        "prev_value": prev,
        "title": "美国 {}年{}月 {}{}{:.1f}%".format(y, m, value_desc, direction_word(v), abs(v)),
        "detail": "前值 {:.1f}%".format(prev) if prev is not None else "",
        "source": US_SOURCE.format(page_no),
    }


def fetch_fed_rate():
    rows = get_json(em_params(filter='(INDICATOR_ID="EMG00342250")'))["result"]["data"]
    row = latest_published(rows)
    v, prev = row["VALUE"], row.get("PRE_VALUE")
    changed = prev is not None and abs(v - prev) > 1e-9
    y, m = row["REPORT_DATE"][:4], month_of(row["REPORT_DATE"])
    if changed:
        bp = round((v - prev) * 100)
        act = "加息" if bp > 0 else "降息"
        title = "美联储{} {} 个基点，联邦基金利率目标上限至 {:.2f}%".format(act, abs(bp), v)
    else:
        title = "美联储维持联邦基金利率目标上限 {:.2f}% 不变".format(v)
    return {
        "report_key": row["REPORT_DATE"][:10],
        "publish_date": date_of(row["PUBLISH_DATE"]) if row.get("PUBLISH_DATE") else None,
        "value": v,
        "prev_value": prev,
        "changed": changed,
        "title": title,
        "detail": "前值 {:.2f}%".format(prev) if prev is not None else "",
        "source": "https://data.eastmoney.com/cjsj/globalRateUSA.html",
    }


# ---------- 主流程 ----------

def load_json(path, default):
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return default


def save_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)


SPECS = [
    # key, kind, country, category, fetcher
    ("cn_cpi", "release", "中国", "通胀", fetch_cn_cpi),
    ("cn_ppi", "release", "中国", "通胀", fetch_cn_ppi),
    ("lpr", "rate", "中国", "货币政策", fetch_lpr),
    ("us_cpi", "release", "美国", "通胀", lambda: fetch_us_indicator("EMG00000733", "0_12", "CPI 同比")),
    ("us_ppi", "release", "美国", "通胀", lambda: fetch_us_indicator("EMG00177897", "0_6", "PPI 环比")),
    ("fed", "rate", "美国", "货币政策", fetch_fed_rate),
]


def main():
    state = load_json(STATE_FILE, {})
    events = load_json(EVENTS_FILE, [])
    seen = {e["id"] for e in events}
    latest = {}
    now = datetime.now(CN_TZ).strftime("%Y-%m-%d %H:%M")
    first_run = not state
    new_events = []

    for key, kind, country, category, fetcher in SPECS:
        try:
            info = fetcher()
        except Exception as exc:  # 单个指标失败不影响其他
            print("[warn] {} failed: {}".format(key, exc))
            continue

        publish = info.get("publish_date") or info["report_key"]
        latest[key] = {
            "title": info["title"], "detail": info["detail"],
            "value": info.get("value"), "date": publish, "source": info["source"],
        }

        should_emit = False
        if kind == "release":
            should_emit = state.get(key) != info["report_key"]
        else:  # rate：只在读数变化时记事件（首次运行记最近一次变化）
            should_emit = bool(info.get("changed")) or first_run and info.get("changed")

        if should_emit:
            eid = "{}:{}".format(key, info["report_key"])
            if eid not in seen:
                events.append({
                    "id": eid,
                    "date": publish,
                    "country": country,
                    "category": category,
                    "title": info["title"],
                    "detail": info["detail"],
                    "source": info["source"],
                })
                seen.add(eid)
                new_events.append(eid)

        state[key] = info["report_key"]
        if kind == "rate":
            state[key + "_value"] = info.get("value")

    events.sort(key=lambda e: e["date"], reverse=True)
    del events[300:]  # 只保留最近 300 条

    save_json(STATE_FILE, state)
    save_json(EVENTS_FILE, events)
    save_json(LATEST_FILE, {"updated_at": now, "items": latest})

    print("[ok] {} events total, {} new: {}".format(len(events), len(new_events), ", ".join(new_events) or "none"))


if __name__ == "__main__":
    main()
