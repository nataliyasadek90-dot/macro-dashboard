#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
daily_report.py
GeoAnalyst 策略日报自动生成脚本
每天早上9点运行：生成MD日报 + 更新网站
"""

import os
import json
import requests
import re
from datetime import datetime, timedelta, timezone
import pytz

# ─── 配置（优先读环境变量，兜底用硬编码，兼容本地和 GitHub Actions）───
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
REPORTS_DIR = os.path.join(BASE_DIR, "daily_reports")
FRED_KEY = os.environ.get("FRED_KEY", "992d1767abdb338df30158b1973eac39")
NEWS_API_KEY = os.environ.get("NEWS_API_KEY", "8a45cb3ebe6d4e9cab4650aedcaacf68")

# 飞书推送配置
FEISHU_APP_ID = os.environ.get("FEISHU_APP_ID", "cli_a958c057e6399ccd")
FEISHU_APP_SECRET = os.environ.get("FEISHU_APP_SECRET", "nBP74o8WNz38siewXlgsVezXrakDVvQD")
FEISHU_OPEN_ID = os.environ.get("FEISHU_OPEN_ID", "ou_3988cd51a4b8ffa564d4eb72cd5e6687")

CST = pytz.timezone("Asia/Shanghai")
ET = pytz.timezone("America/New_York")

os.makedirs(REPORTS_DIR, exist_ok=True)


# ─── 1A. 输出实时行情 JSON（供日报网站 ticker 使用）───

def write_market_live_json(assets: dict):
    """将市场数据写入 market_live.json，HTML 每60秒拉取一次"""
    # 格式化涨跌幅方向
    def chg_dir(val):
        if val is None or val == "N/A": return "flat"
        return "up" if val > 0 else "down"

    def fmt(val, decimals=2, prefix="$"):
        if val is None or val == "N/A": return "N/A"
        if isinstance(val, str): return val
        return f"{prefix}{val:,.{decimals}f}"

    def fmt_chg(val):
        if val is None: return "--"
        if val == 0: return "→ 平"
        arrow = "↑" if val > 0 else "↓"
        sign = "+" if val > 0 else ""
        return f"{sign}{val:.2f}% {arrow}"

    gold    = assets.get("黄金", {})
    brent   = assets.get("布伦特原油", {})
    wti     = assets.get("WTI原油", {})
    sp500   = assets.get("S&P500", {})
    ndx     = assets.get("纳斯达克", {})
    vix     = assets.get("VIX", {})
    ust10   = assets.get("10Y美债_FRED", "N/A")
    dxy     = assets.get("美元指数", {})
    csi300  = assets.get("沪深300", {})
    xle     = assets.get("XLE能源", {})
    ust20   = assets.get("20Y美债_FRED", "N/A")

    tickers = [
        {"name": "黄金",    "symbol": "GC=F",     "price": fmt(gold.get("price")),    "change": fmt_chg(gold.get("change_pct")),    "dir": chg_dir(gold.get("change_pct"))},
        {"name": "WTI",     "symbol": "CL=F",     "price": fmt(wti.get("price")),     "change": fmt_chg(wti.get("change_pct")),     "dir": chg_dir(wti.get("change_pct"))},
        {"name": "布伦特",  "symbol": "BZ=F",     "price": fmt(brent.get("price")),   "change": fmt_chg(brent.get("change_pct")),   "dir": chg_dir(brent.get("change_pct"))},
        {"name": "标普500", "symbol": "^GSPC",    "price": fmt(sp500.get("price"), prefix=""), "change": fmt_chg(sp500.get("change_pct")), "dir": chg_dir(sp500.get("change_pct"))},
        {"name": "纳指100", "symbol": "^IXIC",   "price": fmt(ndx.get("price"), prefix=""),   "change": fmt_chg(ndx.get("change_pct")),   "dir": chg_dir(ndx.get("change_pct"))},
        {"name": "沪深300", "symbol": "000300.SS","price": fmt(csi300.get("price"), prefix=""),"change": fmt_chg(csi300.get("change_pct")),"dir": chg_dir(csi300.get("change_pct"))},
        {"name": "10Y美债", "symbol": "^TNX",    "price": f"{ust10}%", "change": "—", "dir": "flat"},
        {"name": "20Y美债", "symbol": "^FXXD",   "price": f"{ust20}%", "change": "—", "dir": "flat"},
        {"name": "VIX",     "symbol": "^VIX",    "price": fmt(vix.get("price"), prefix=""),  "change": fmt_chg(vix.get("change_pct")),  "dir": chg_dir(vix.get("change_pct"))},
        {"name": "美元指数","symbol": "DX-Y.NYB","price": fmt(dxy.get("price"), prefix=""), "change": fmt_chg(dxy.get("change_pct")),  "dir": chg_dir(dxy.get("change_pct"))},
        {"name": "XLE",     "symbol": "XLE",     "price": fmt(xle.get("price")),    "change": fmt_chg(xle.get("change_pct")),    "dir": chg_dir(xle.get("change_pct"))},
    ]

    live = {
        "tickers": tickers,
        "updated_at": datetime.now(CST).isoformat(),
        "source": "GeoAnalyst · 腾讯证券(美指) / 新浪财经(商品) / FRED / 新浪指数(沪深)",
    }

    live_path = os.path.join(REPORTS_DIR, "market_live.json")
    with open(live_path, "w", encoding="utf-8") as f:
        json.dump(live, f, ensure_ascii=False, indent=2)
    print(f"  [网站] market_live.json 已更新 ({datetime.now(CST).strftime('%H:%M:%S')})")


# ─── 1. 数据采集 ───

def get_fred_data(series_id, days=5):
    """从FRED获取指标数据"""
    url = (
        f"https://api.stlouisfed.org/fred/series/observations"
        f"?series_id={series_id}&api_key={FRED_KEY}&file_type=json&limit={days}"
    )
    try:
        r = requests.get(url, timeout=15)
        obs = r.json()["observations"]
        valid = [(o["date"], o["value"]) for o in obs if o["value"] != "."]
        return valid[-1] if valid else (None, "N/A")
    except Exception as e:
        print(f"  [FRED] {series_id} 获取失败: {e}")
        return (None, "N/A")


def get_sina_data(sina_code, name="", unit=""):
    """
    从新浪财经获取国际市场数据（黄金/原油/股指等）
    
    Sina代码映射:
    - 黄金GC: hf_GC
    - WTI原油: hf_CL
    - 布伦特: hf_BZ  (部分支持)
    - 标普500: hf_ES
    - 纳斯达克: hf_NQ
    - 白银: hf_SI
    - 铜: hf_HG
    - 天然气: hf_NG
    
    返回: {"price": float, "prev_close": float, "change_pct": float, "symbol": str}
    """
    url = f"https://hq.sinajs.cn/list={sina_code}"
    headers = {
        "Referer": "http://finance.sina.com.cn",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    }
    for attempt in range(3):
        try:
            r = requests.get(url, headers=headers, timeout=10)
            txt = r.text.strip()
            
            # 解析: var hq_str_hf_GC="4786.5,4800.0,4760.0,4790.0,4785.0,4760.0,4786.5,4760.0,..."
            if f'hq_str_{sina_code}' not in txt:
                raise ValueError(f"返回格式不符: {txt[:60]}")
            
            content = txt.split('"')[1]
            parts = content.split(',')
            if not parts or not parts[0].strip():
                raise ValueError(f"数据为空: {txt[:60]}")
            
            current = float(parts[0])
            # parts[1]通常是昨收价，但有时是今日最高/最低
            # 尝试找到合理的昨收价（通常是前几个字段之一）
            prev_close = None
            for i in range(1, min(6, len(parts))):
                try:
                    v = float(parts[i])
                    # 昨收价应该和当前价相近（10%范围内）
                    if 0.9 * current <= v <= 1.1 * current:
                        prev_close = v
                        break
                except (ValueError, IndexError):
                    continue
            
            if prev_close is None:
                prev_close = current  # 无法获取昨收，保守设为当前价
            
            change_pct = ((current - prev_close) / prev_close * 100) if prev_close else 0
            
            return {
                "price": round(current, 2),
                "prev_close": round(prev_close, 2),
                "change_pct": round(change_pct, 2),
                "symbol": sina_code,
            }
        except Exception as e:
            if attempt < 2:
                import time; time.sleep(1.5 ** attempt)
            else:
                print(f"  [Sina] {name}({sina_code}) 3次重试后失败: {e}")
                return {"price": None, "change_pct": 0, "symbol": sina_code}
    return {"price": None, "change_pct": 0, "symbol": sina_code}


def get_fred_vix():
    """从FRED获取VIX恐慌指数（含历史昨收计算涨跌幅）"""
    url = "https://api.stlouisfed.org/fred/series/observations"
    params = {
        "series_id": "VIXCLS",
        "api_key": FRED_KEY,
        "file_type": "json",
        "limit": 2  # 今天+昨天
    }
    try:
        r = requests.get(url, params=params, timeout=10)
        obs = r.json().get("observations", [])
        if len(obs) >= 2:
            today_val = float(obs[-1]["value"])
            prev_val = float(obs[-2]["value"])
            change_pct = ((today_val - prev_val) / prev_val * 100) if prev_val else 0
            return {
                "price": round(today_val, 2),
                "prev_close": round(prev_val, 2),
                "change_pct": round(change_pct, 2),
                "symbol": "VIXCLS",
            }
        elif len(obs) == 1:
            val = float(obs[0]["value"])
            return {"price": round(val, 2), "prev_close": round(val, 2), "change_pct": 0, "symbol": "VIXCLS"}
    except Exception as e:
        print(f"  [FRED VIX] 获取失败: {e}")
    return {"price": None, "change_pct": 0, "symbol": "VIXCLS"}


def get_fred_dxy():
    """从FRED获取美元指数DXY（含历史昨收计算涨跌幅）"""
    url = "https://api.stlouisfed.org/fred/series/observations"
    params = {
        "series_id": "DTWEXBGS",  # Broad Dollar Index
        "api_key": FRED_KEY,
        "file_type": "json",
        "limit": 2
    }
    try:
        r = requests.get(url, params=params, timeout=10)
        obs = r.json().get("observations", [])
        if len(obs) >= 2:
            today_val = float(obs[-1]["value"])
            prev_val = float(obs[-2]["value"])
            change_pct = ((today_val - prev_val) / prev_val * 100) if prev_val else 0
            return {
                "price": round(today_val, 2),
                "prev_close": round(prev_val, 2),
                "change_pct": round(change_pct, 2),
                "symbol": "DTWEXBGS",
            }
        elif len(obs) == 1:
            val = float(obs[0]["value"])
            return {"price": round(val, 2), "prev_close": round(val, 2), "change_pct": 0, "symbol": "DTWEXBGS"}
    except Exception as e:
        print(f"  [FRED DXY] 获取失败: {e}")
    return {"price": None, "change_pct": 0, "symbol": "DTWEXBGS"}


def get_yahoo_data(symbol, name=""):
    """从Yahoo Finance获取单个资产价格（备用数据源）"""
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    for attempt in range(3):
        try:
            r = requests.get(url, headers=headers, timeout=15)
            data = r.json()["chart"]["result"][0]
            meta = data["meta"]
            price = meta.get("regularMarketPrice", 0)
            prev  = meta.get("chartPreviousClose", 0) or meta.get("regularMarketPreviousClose", 0)
            change_pct = ((price - prev) / prev * 100) if prev else 0
            return {
                "price": round(price, 2),
                "prev_close": round(prev, 2),
                "change_pct": round(change_pct, 2),
                "symbol": symbol,
            }
        except Exception as e:
            if attempt < 2:
                import time; time.sleep(2 ** attempt)  # 指数退避 2s, 4s
            else:
                print(f"  [Yahoo] {symbol}({name}) 备用源失败: {e}")
                return {"price": None, "change_pct": 0, "symbol": symbol}
    return {"price": None, "change_pct": 0, "symbol": symbol}


def get_tencent_usindex(tencent_code, name=""):
    """
    从腾讯证券获取美股/港股现货指数（含昨收价和涨跌幅）
    
    腾讯代码:
    - usNDX: 纳斯达克100现货指数
    - usSPX: 标普500现货指数
    
    字段解析:
    [3]=当前价 [4]=昨收 [32]=涨跌幅(%) [33]=最高 [34]=最低 [30]=更新时间
    
    返回: {"price": float, "prev_close": float, "change_pct": float, "symbol": str}
    """
    url = f"https://qt.gtimg.cn/q={tencent_code}"
    headers = {
        "Referer": "https://finance.qq.com",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
    }
    for attempt in range(3):
        try:
            r = requests.get(url, headers=headers, timeout=10)
            txt = r.text.strip()
            
            if f'="{tencent_code}' not in txt and tencent_code not in txt:
                raise ValueError(f"返回格式不符: {txt[:60]}")
            
            # 解析腾讯数据: "v_usNDX=\"200~纳斯达克100~.NDX~24991.59~24903.17~..."
            content = txt.split('="')[1].rstrip('";')
            parts = content.split('~')
            
            if len(parts) < 33:
                raise ValueError(f"字段不足({len(parts)}): {txt[:80]}")
            
            current = float(parts[3])   # 当前价
            prev_close = float(parts[4])  # 昨收
            change_pct = float(parts[32])  # 涨跌幅(%)
            
            return {
                "price": round(current, 2),
                "prev_close": round(prev_close, 2),
                "change_pct": round(change_pct, 2),
                "symbol": tencent_code,
            }
        except Exception as e:
            if attempt < 2:
                import time; time.sleep(1.5 ** attempt)
            else:
                print(f"  [腾讯] {name}({tencent_code}) 获取失败: {e}")
                return {"price": None, "change_pct": 0, "symbol": tencent_code}
    return {"price": None, "change_pct": 0, "symbol": tencent_code}


def get_sina_stock_index(sina_code, name=""):
    """
    从新浪财经获取股票指数（含昨收价）
    
    新浪股票指数接口(s_前缀)格式:
    var hq_str_s_sh000300="沪深300,4566.2237,-29.3323,-0.64,...";
    字段: [0]=名称 [1]=当前价 [2]=涨跌额 [3]=涨跌幅 [4]=成交量 [5]=成交额
    
    返回: {"price": float, "prev_close": float, "change_pct": float, "symbol": str}
    """
    url = f"https://hq.sinajs.cn/list={sina_code}"
    headers = {
        "Referer": "http://finance.sina.com.cn",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
    }
    for attempt in range(3):
        try:
            r = requests.get(url, headers=headers, timeout=10)
            txt = r.text.strip()
            
            if f'hq_str_{sina_code}' not in txt:
                raise ValueError(f"返回格式不符: {txt[:60]}")
            
            content = txt.split('"')[1]
            parts = content.split(',')
            if len(parts) < 4:
                raise ValueError(f"字段不足({len(parts)}): {txt[:80]}")
            
            current = float(parts[1])        # 当前价
            change_pct = float(parts[3])     # 涨跌幅(%)
            prev_close = current / (1 + change_pct / 100)  # 反推昨收价
            
            return {
                "price": round(current, 2),
                "prev_close": round(prev_close, 2),
                "change_pct": round(change_pct, 2),
                "symbol": sina_code,
            }
        except Exception as e:
            if attempt < 2:
                import time; time.sleep(1.5 ** attempt)
            else:
                print(f"  [Sina指数] {name}({sina_code}) 获取失败: {e}")
                return {"price": None, "change_pct": 0, "symbol": sina_code}
    return {"price": None, "change_pct": 0, "symbol": sina_code}


def get_csi300():
    """通过akshare获取沪深300"""
    try:
        import akshare as ak
        spot = ak.stock_zh_index_spot_em()
        row = spot[spot["代码"] == "000300"].iloc[0]
        return {
            "price": float(row["最新价"]),
            "change_pct": float(row["涨跌幅"]),
            "symbol": "000300",
        }
    except Exception as e:
        print(f"  [akshare] 沪深300 获取失败: {e}")
        return get_yahoo_data("000300.SS", "沪深300")


def get_news_headlines():
    """
    获取当日重要新闻 - 三级兜底（永不失败）：
    1. 新浪财经财经要闻（免费，无需Key，无限次，主力来源）
    2. GDELT全球新闻API（免费，无Key，每5秒限1次，英文宏观补充）
    3. NewsAPI（免费版每天100次，真实最后保底，避免轻易触发）
    """
    import time

    # ── 第一级：新浪财经财经要闻（3页，扩展关键词） ──
    KEYWORDS_CN = [
        '黄金', '原油', '石油', '通胀', 'CPI', 'PPI', '美联储', '加息', '降息',
        '地缘', '冲突', '战争', '制裁', 'OPEC', '美债', '纳斯达克', '标普',
        '沪深', 'A股', '大宗商品', '能源', '天然气', '铜', '原油',
        '经济', '市场', '汇市', '汇价', '股市', '汇率', '美元', '日元', '欧元',
        '央行', '鲍威尔', '衰退', '恐慌', 'VIX', '美股',
        '油价', '金价', '期货', '商品', '小麦', '玉米',
        '俄乌', '中东', '伊朗', '以色列', '胡塞', '红海', '减产',
        '议息', '非农', '就业', '零售', 'PMI', 'ISM', 'GDP',
        '关税', '科技股', 'AI', '英伟达',
    ]

    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
                'Referer': 'https://finance.sina.com.cn'}

    all_items = []
    seen_titles = set()

    # lid=2516 是财经要闻主频道，抓3页（共60条）
    for page in [1, 2, 3]:
        try:
            r = requests.get(
                'https://feed.mix.sina.com.cn/api/roll/get',
                params={'pageid': 153, 'lid': 2516, 'num': 20, 'page': page},
                headers=headers, timeout=8
            )
            data = r.json()
            items = data.get('result', {}).get('data', [])
            if not items:
                break
            for item in items:
                title = item.get('title', '').strip()
                if not title or title in seen_titles:
                    continue
                if any(kw in title for kw in KEYWORDS_CN):
                    all_items.append({
                        'title': title,
                        'source': item.get('media_name') or '财经要闻',
                        'url': item.get('url', ''),
                        'ctime': int(item.get('ctime', 0))
                    })
                    seen_titles.add(title)
        except Exception as e:
            print(f"  [新浪财经 page={page}] 获取失败: {e}")
            break

    if len(all_items) >= 3:
        all_items.sort(key=lambda x: x['ctime'], reverse=True)
        print(f"  [新浪财经] 获得 {len(all_items)} 条相关新闻")
        return [{'title': x['title'], 'source': x['source'], 'url': x['url']} for x in all_items[:8]]

    # ── 第二级：GDELT（英文宏观新闻，免费，无需Key） ──
    print("  [新浪财经] 新闻偏少，尝试 GDELT 英文宏观新闻...")
    time.sleep(1)  # GDELT 要求每5秒最多1次
    try:
        r = requests.get(
            'https://api.gdeltproject.org/api/v2/doc/doc',
            params={
                'format': 'json',
                'maxrecords': 10,
                'mode': 'artlist',
                'query': '(oil OR gold OR inflation OR Fed OR CPI OR geopolitical OR stock market)',
                'lang': 'English',
                'sort': 'DateDesc'
            },
            timeout=15
        )
        gdelt_data = r.json()
        articles = gdelt_data.get('articles', []) if isinstance(gdelt_data, dict) else []
        if articles:
            result = [
                {'title': a.get('title', ''), 'source': a.get('domain', ''), 'url': a.get('url', '')}
                for a in articles[:6]
                if a.get('title')
            ]
            if result:
                print(f"  [GDELT] 补充 {len(result)} 条英文新闻")
                return result
    except Exception as e:
        print(f"  [GDELT] 获取失败: {e}")

    # ── 第三级：NewsAPI（最后保底，有每日100次限制，谨慎使用） ──
    print("  [GDELT] 无数据，尝试 NewsAPI（此源日限额100次，非必要不触发）...")
    now_cst = datetime.now(CST)
    from_dt = (now_cst - timedelta(hours=24)).strftime("%Y-%m-%dT%H:%M:%S")
    url = (
        f"https://newsapi.org/v2/everything"
        f"?q=oil+gold+inflation+Fed+CPI+market+geopolitics"
        f"&from={from_dt}&sortBy=publishedAt"
        f"&language=en&pageSize=8"
        f"&apiKey={NEWS_API_KEY}"
    )
    try:
        r = requests.get(url, timeout=15)
        articles = r.json().get("articles", [])
        result = [
            {'title': a["title"], 'source': a["source"]["name"], 'url': a["url"]}
            for a in articles[:8] if a.get("title")
        ]
        if result:
            print(f"  [NewsAPI] 获得 {len(result)} 条（免费版日限额100次）")
        return result
    except Exception as e:
        print(f"  [NewsAPI] 获取失败: {e}")
        return []


def collect_market_data():
    """
    汇集所有市场数据
    
    数据源优先级:
    1. 新浪财经 (sina.com.cn) — 国际大宗商品/股指，主力数据源
    2. FRED — VIX、美元指数、美债收益率
    3. akshare — 沪深300
    4. Yahoo Finance — 备用（沙盒环境可能403）
    """
    print("📊 正在采集市场数据...")

    assets = {}

    # ── 新浪财经（Sina）大宗商品主数据源 ──
    sina_map = {
        "hf_GC":  "黄金",
        "hf_CL":  "WTI原油",
        "hf_OIL": "布伦特原油",
        "hf_SI":  "白银",
        "hf_HG":  "伦铜",
        "hf_NG":  "天然气",
    }
    for code, name in sina_map.items():
        d = get_sina_data(code, name)
        if d.get("price") is None:
            print(f"  [备用] {name} 切换到Yahoo Finance...")
            sina_to_yahoo = {
                "hf_GC": "GC=F", "hf_CL": "CL=F", "hf_SI": "SI=F",
                "hf_HG": "HG=F", "hf_NG": "NG=F",
            }
            d = get_yahoo_data(sina_to_yahoo.get(code, code), name)
        assets[name] = d

    # ── 美股指数：腾讯证券现货（含昨收）──
    assets["纳斯达克"] = get_tencent_usindex("usNDX", "纳指100")
    assets["S&P500"]   = get_tencent_usindex("usINX", "标普500")

    # ── FRED 数据源 ──
    assets["VIX"] = get_fred_vix()
    assets["美元指数"] = get_fred_dxy()

    # 美债收益率
    _, dgs10 = get_fred_data("DGS10", 3)
    _, dgs20 = get_fred_data("DGS20", 3)
    assets["10Y美债_FRED"] = dgs10
    assets["20Y美债_FRED"] = dgs20

    # ── 沪深300：新浪股票指数接口（含昨收）──
    assets["沪深300"] = get_sina_stock_index("s_sh000300", "沪深300")

    # ── XLE 能源板块：复合计算（WTI + 天然气）──
    wti = assets.get("WTI原油", {}).get("price")
    ng = assets.get("天然气", {}).get("price")
    wti_prev = assets.get("WTI原油", {}).get("prev_close")
    ng_prev = assets.get("天然气", {}).get("prev_close")
    if wti and ng:
        # XLE ≈ 60%石油 + 40%天然气的复合价格
        xle_price = wti * 0.6 + ng * 2.5 * 0.4  # 天然气单位换算
        xle_prev = (wti_prev or wti) * 0.6 + (ng_prev or ng) * 2.5 * 0.4
        assets["XLE能源"] = {
            "price": round(xle_price, 2),
            "prev_close": round(xle_prev, 2),
            "change_pct": round((xle_price - xle_prev) / xle_prev * 100, 2) if xle_prev else 0,
            "symbol": "XLE_est",
        }
    else:
        assets["XLE能源"] = {"price": None, "change_pct": 0, "symbol": "XLE_est"}

    return assets


# ─── 2. 计算Panic Index ───

def compute_panic_index(assets):
    """根据市场数据计算Panic Index (0-100)"""
    score = 0

    # VIX因子 (权重30%)
    vix = assets.get("VIX", {}).get("price") or 20
    if vix >= 30:   vix_score = 90
    elif vix >= 25: vix_score = 75
    elif vix >= 20: vix_score = 60
    elif vix >= 15: vix_score = 40
    else:           vix_score = 25
    score += vix_score * 0.30

    # 通胀因子 (权重25%) - 基于BEI估算
    ust10 = float(assets.get("10Y美债_FRED", "4.3") or 4.3)
    # 近似: BEI = 10Y名义 - 0.25(TIPS利差), 用收益率高低代表通胀预期
    if ust10 >= 4.6:  infl_score = 85
    elif ust10 >= 4.3: infl_score = 65
    elif ust10 >= 4.0: infl_score = 50
    else:              infl_score = 35
    score += infl_score * 0.25

    # 地缘因子 (权重30%) - 用油价偏离基准来估算
    brent = assets.get("布伦特原油", {}).get("price") or 95
    if brent >= 115:   geo_score = 90
    elif brent >= 105: geo_score = 75
    elif brent >= 95:  geo_score = 55
    elif brent >= 85:  geo_score = 35
    else:              geo_score = 20
    score += geo_score * 0.30

    # 权益因子 (权重15%) - 用标普日涨跌
    sp_chg = assets.get("S&P500", {}).get("change_pct") or 0
    if sp_chg <= -2:   eq_score = 80
    elif sp_chg <= -1: eq_score = 65
    elif sp_chg <= 0:  eq_score = 50
    elif sp_chg <= 1:  eq_score = 40
    else:              eq_score = 30
    score += eq_score * 0.15

    return round(score)


# ─── 3. 判断CPI路径 ───

def get_cpi_scenario():
    """返回CPI路径状态描述（静态配置，可后期接入API更新）"""
    # 这里可扩展为读取历史CPI文件或从API获取
    return {
        "path_a_prob": 35,
        "path_b_prob": 42,
        "path_c_prob": 23,
        "next_cpi_date": "2026年4月10日",
        "active_path": "B",
        "note": "路径B为基准：CPI 3.0%-3.3% YoY，概率42%"
    }


# ─── 4. 生成日报MD ───

def fmt_chg(val, suffix=""):
    """格式化涨跌幅"""
    if val is None: return "N/A"
    arrow = "↑" if val > 0 else ("↓" if val < 0 else "→")
    sign  = "+" if val > 0 else ""
    return f"{sign}{val:.2f}%{suffix} {arrow}"


def fmt_price(val, prefix="$", decimals=2):
    if val is None: return "N/A"
    return f"{prefix}{val:,.{decimals}f}"


def generate_md(report_date_cst, assets, headlines):
    """
    生成日报Markdown内容 - 严格遵循「4月6日架构」三大协议：
    1. 9个固定模块，顺序不可乱
    2. [Nav_Summary] 前置字段（≤15字，禁止为空/仅日期）
    3. Event-Rolling Protocol：数据公布后立即复盘+滚动
    """
    date_str = report_date_cst.strftime("%Y年%m月%d日")
    data_start = (report_date_cst - timedelta(days=1)).strftime("%Y年%m月%d日")
    weekday_map = "一二三四五六日"
    weekday = "周" + weekday_map[report_date_cst.weekday()]

    panic = compute_panic_index(assets)
    cpi = get_cpi_scenario()

    gold   = assets.get("黄金", {})
    brent  = assets.get("布伦特原油", {})
    wti    = assets.get("WTI原油", {})
    sp500  = assets.get("S&P500", {})
    ndx    = assets.get("纳斯达克", {})
    vix    = assets.get("VIX", {})
    ust10  = assets.get("10Y美债_FRED", "N/A")
    dxy    = assets.get("美元指数", {})
    csi300 = assets.get("沪深300", {})
    xle    = assets.get("XLE能源", {})

    def anchor_tag(val, warn_t, danger_t):
        try: v = float(val)
        except: return "⚪ 无数据"
        if v >= danger_t: return "🔴 " + str(val)
        elif v >= warn_t: return "🟡 " + str(val)
        else: return "🟢 " + str(val)

    vix_state  = anchor_tag(vix.get('price', 0), 20, 25)
    brent_state = anchor_tag(brent.get('price', 0), 100, 110)
    ust10_state = anchor_tag(ust10 if ust10 not in ("N/A", None) else 0, 4.3, 4.6)

    if panic >= 80:   risk_level = "🔴 高风险"
    elif panic >= 60: risk_level = "🟡 中高风险"
    else:             risk_level = "🟢 中等风险"

    brent_price = brent.get('price') or 95
    vix_price   = vix.get('price') or 15

    if brent_price >= 105:
        nav_core = "油价暴涨·地缘升温"
    elif vix_price >= 25:
        nav_core = "VIX飙升·市场恐慌"
    elif brent_price <= 80:
        nav_core = "停火生效·油价回落"
    else:
        nav_core = "CPI路径" + str(cpi['active_path']) + "·等待数据"

    if brent_price >= 95:
        geo_chain = "D40海峡封锁持续，航运绕行导致原油运输成本上升，现货溢价扩大至$3/桶。"
        infl_dir, ust_dir = "上升", "跟随上行"
        trend_gold, trend_risk = "受益", "承压"
    elif brent_price <= 85:
        geo_chain = "D40停火协议持续，原油供应预期改善，封锁溢价消退。"
        infl_dir, ust_dir = "下降", "回落"
        trend_gold, trend_risk = "承压", "受益"
    else:
        geo_chain = "地缘局势边际缓和，原油供应预期改善，但不确定犹存。"
        infl_dir, ust_dir = "平稳", "区间震荡"
        trend_gold, trend_risk = "震荡", "震荡"

    e1_title = ("暂无重大事件" if not headlines
                else headlines[0].get('source', '') + "：" + headlines[0].get('title', '')[:30])
    e2_title = ("—" if len(headlines) < 2
                 else headlines[1].get('source', '') + "：" + headlines[1].get('title', '')[:30])

    def t_up(v):   return "↑↑" if (v or 0) > 1 else "↑" if (v or 0) > 0 else "↓"
    def t_dir(v): return "↑" if (v or 0) > 0 else "↓"
    def vix_arr(p):
        p = p or 0
        if p > 25: return "🔴↑"
        elif p > 20: return "🟡"
        else: return "✅↓"

    def fp(val, prefix="$", decimals=2):
        if val is None: return "N/A"
        try: return prefix + f"{float(val):.2f}"
        except: return str(val)

    def fchg(val):
        if val is None: return "—"
        try:
            v = float(val)
            sign = "+" if v >= 0 else ""
            return f"{sign}{v:.2f}%"
        except: return "—"

    next5 = [
        (cpi.get('next_cpi_date', 'TBD'), '🇺🇸 美国CPI发布', '⭐⭐⭐⭐⭐'),
        ('4月22日', '🕊️ 临时停火协议到期', '⭐⭐⭐⭐'),
        ('4月28-29日', '🏛️ FOMC 利率决议', '⭐⭐⭐⭐⭐'),
        ((report_date_cst + timedelta(days=5)).strftime("%m月%d日"), '📊 美国零售销售数据', '⭐⭐⭐'),
        ((report_date_cst + timedelta(days=7)).strftime("%m月%d日"), '🏭 欧元区CPI终值', '⭐⭐⭐'),
    ]
    cal_rows = "\n".join("| " + d + " | " + e + " | " + i + " |" for d, e, i in next5)

    panic_geo = min(95, max(20, int((brent.get('price') or 95) - 70) * 2 + 20))
    panic_vix = min(95, max(20, int((vix.get('price') or 20) * 3)))
    panic_cyt = 65 if float(ust10 or 4.3) >= 4.3 else 50
    panic_eq  = max(30, 50 + int(-(sp500.get('change_pct') or 0) * 15))

    gold_p   = fp(gold.get('price'))
    gold_c   = fchg(gold.get('change_pct'))
    brent_p  = fp(brent.get('price'))
    brent_c  = fchg(brent.get('change_pct'))
    wti_p    = fp(wti.get('price'))
    wti_c    = fchg(wti.get('change_pct'))
    sp5_p    = fp(sp500.get('price'), '', 2)
    sp5_c    = fchg(sp500.get('change_pct'))
    ndx_p    = fp(ndx.get('price'), '', 2)
    ndx_c    = fchg(ndx.get('change_pct'))
    vix_p    = fp(vix.get('price'), '', 2)
    vix_c    = fchg(vix.get('change_pct'))
    dxy_p    = fp(dxy.get('price'), '', 2)
    dxy_c    = fchg(dxy.get('change_pct'))
    csi3_p   = fp(csi300.get('price'), '', 2)
    csi3_c   = fchg(csi300.get('change_pct'))
    xle_p    = fp(xle.get('price'))
    xle_c    = fchg(xle.get('change_pct'))

    news_block = ""
    if headlines:
        for h in headlines[:8]:
            src = h.get('source', '来源')
            ttl = h.get('title', '')
            news_block += "- **" + src + "**: " + ttl + "\n"
    else:
        news_block = "- 暂无新闻数据（所有新闻源均不可用）\n"

    md = """[Nav_Summary] """ + nav_core + """

---

# 📊 策略日报 | """ + date_str + """

> **数据窗口**：**""" + data_start + " 09:00 → " + date_str + """ 09:00 （""" + weekday + """）
> **发布时间**：""" + date_str + """
> **分析师**：GeoAnalyst 🌍

---

## 🔥 核心结论

> 数据自动生成 · 由GeoAnalyst宏观策略引擎驱动

当前 Panic Index 为 **""" + str(panic) + """**，风险等级：**""" + risk_level + """**。
CPI基准情景为**路径""" + str(cpi['active_path']) + """**（""" + str(cpi['note']) + """）。
**""" + nav_core + """**是今日市场主线。

---

## ⚡ 重大事件

| 事件 | 时间 | 影响等级 |
|:---|:---:|:---:|
| """ + e1_title + """ | """ + date_str + """ | ⭐⭐⭐ |
| """ + e2_title + """ | — | ⭐⭐ |

---

## 🔗 宏观逻辑传导链

**链条1：地缘 → 能源供应 → 通胀预期 → 避险情绪**

> """ + geo_chain + """
→ 通胀预期""" + infl_dir + """，美债收益率""" + ust_dir + """。
→ 避险资产（黄金、美债）""" + trend_gold + """，风险资产（纳指、沪深300）""" + trend_risk + """。

**链条2：CPI数据预期 → FOMC降息路径 → 美元指数 → 全球流动性**

> 市场定价""" + str(cpi['active_path']) + """路径：""" + str(cpi['note']) + """。
→ 若路径B（3.0%-3.3%）：美联储维持观望，美元温和走弱。
→ 黄金、原油获得支撑，新兴市场流动性改善。

---

## 📊 市场数据快照

| 资产 | 价格 | 日涨跌 | 趋势 |
|:---:|:---:|:---:|:---:|
| 🥇 黄金 | """ + gold_p + """ | """ + gold_c + """ | """ + t_up(gold.get('change_pct')) + """ |
| 🛢️ 布伦特原油 | """ + brent_p + """ | """ + brent_c + """ | """ + t_dir(brent.get('change_pct')) + """ |
| 🛢️ WTI原油 | """ + wti_p + """ | """ + wti_c + """ | """ + t_dir(wti.get('change_pct')) + """ |
| 📈 S&P 500 | """ + sp5_p + """ | """ + sp5_c + """ | """ + t_dir(sp500.get('change_pct')) + """ |
| 📊 纳斯达克 | """ + ndx_p + """ | """ + ndx_c + """ | """ + t_dir(ndx.get('change_pct')) + """ |
| 📉 VIX | """ + vix_p + """ | """ + vix_c + """ | """ + vix_arr(vix.get('price')) + """ |
| 💵 10Y美债 | """ + str(ust10) + """% | — | → |
| 💵 美元指数 | """ + dxy_p + """ | """ + dxy_c + """ | """ + t_dir(dxy.get('change_pct')) + """ |
| 🏭 沪深300 | """ + csi3_p + """ [CNY] | """ + csi3_c + """ | """ + t_dir(csi300.get('change_pct')) + """ |
| ⚡ XLE能源 | """ + xle_p + """ | """ + xle_c + """ | """ + t_dir(xle.get('change_pct')) + """ |

---

## 🔥 Panic_Index: """ + str(panic) + """（""" + risk_level + """）

| 因子 | 值估算 | 权重 |
|:---:|:---:|:---:|
| 🌍 地缘（油价偏离基准） | """ + str(panic_geo) + """ | 30% |
| 📊 VIX | """ + str(panic_vix) + """ | 30% |
| 💰 通胀（美债收益率） | """ + str(panic_cyt) + """ | 25% |
| 📉 权益（标普涨跌） | """ + str(panic_eq) + """ | 15% |
| **综合得分** | **""" + str(panic) + """** | — |

---

## 🎯 战术配置与情景矩阵

| 路径 | CPI区间（YoY） | 概率 | 核心策略 |
|:---:|:---:|:---:|:---|
| **路径A** | <3.0% | **""" + str(cpi['path_a_prob']) + """%** | +QQQ/SPY，-DXY，降息预期升温 |
| **路径B ⭐** | **3.0%-3.3%** | **""" + str(cpi['path_b_prob']) + """%** | 黄金25%配置，原油28%，美债+3pp |
| **路径C** | ≥3.3% | **""" + str(cpi['path_c_prob']) + """%** | 清仓纳指，全仓黄金+能源 |

> **当前基准**：路径B（3.0%-3.3%），美联储维持观望，相机而动。

---

## 🚨 触发监控器

| 锚点 | 阈值 | 当前状态 | 触发路径 |
|:---:|:---:|:---:|:---:|
| US10Y 美债 | 4.60% | """ + ust10_state + """ | 路径C风险↑ |
| VIX | 20（警戒）/25（危险） | """ + vix_state + """ | 市场恐慌↑ |
| 布伦特原油 | $100（警戒）/$110（危险） | """ + brent_state + """ | 地缘风险↑ |
| BEI 5Y | 3.00% | — | 通胀预期↑ |

---

## 📰 今日重要新闻

""" + news_block + """
---

## 📅 关键日历（未来5天）

| 日期 | 事件 | 重要度 |
|:---:|:---|:---:|
""" + cal_rows + """

---

⚠️ **风险提示**：本报告由GeoAnalyst自动生成，仅供宏观策略参考，不构成投资建议。数据来源：Yahoo Finance、FRED、akshare。
"""
    return md




def _extract_summary_from_md(md_path: str) -> str:
    """从MD文件中提取第一行标题作为summary"""
    try:
        with open(md_path, "r", encoding="utf-8") as f:
            lines = [l.strip() for l in f.readlines() if l.strip()]
        for line in lines:
            # 跳过 Markdown 标题，找第一段正文
            if line.startswith('#') or line.startswith('>'):
                continue
            # 去掉 Markdown 格式符号
            clean = re.sub(r'[*_`#]', '', line).strip()
            if len(clean) > 8:
                return clean[:40]  # 截断到40字
        return "自动生成日报"
    except Exception:
        return "自动生成日报"


def update_website_manifest(new_entry: dict):
    """将新日报元数据追加到网站的日报列表中（带去重逻辑）"""
    index_path = os.path.join(REPORTS_DIR, "index.html")
    if not os.path.exists(index_path):
        print("  [网站] index.html 不存在，跳过更新")
        return

    with open(index_path, "r", encoding="utf-8") as f:
        content = f.read()

    target_date = new_entry["date"]

    # ── 去重检查：若当日已有条目，不再插入 ──
    import re as _re
    date_pattern = _re.compile(r"date:\s*['\"]" + _re.escape(target_date) + r"['\"]")
    if date_pattern.search(content):
        print(f"  [网站] {new_entry['displayDate']} 已存在于导航，跳过去重")
        return

    # ── 从MD文件提取真实summary ──
    md_path = os.path.join(REPORTS_DIR, new_entry["activeFile"])
    real_summary = _extract_summary_from_md(md_path)
    summary_text = new_entry["summary"]
    if "自动生成" in summary_text and "自动生成日报" not in real_summary:
        summary_text = real_summary

    # ── 生成新条目（挂badge-yellow，等待真实数据覆盖时自动更新）───
    new_report_js = f"""  {{
    date: '{new_entry["date"]}',
    displayDate: '{new_entry["displayDate"]}',
    weekday: '{new_entry["weekday"]}',
    summary: '{summary_text}',
    badge: 'badge-yellow',
    panicIndex: {new_entry["panicIndex"]},
    panicDelta: '自动生成',
    panicLevel: '{new_entry["panicLevel"]}',
    panicLevelText: '{new_entry["panicLevelText"]}',
    pathProbs: [35, 42, 23],
    activeFile: '{new_entry["activeFile"]}'
  }},"""

    content = content.replace(
        "const REPORTS = [",
        f"const REPORTS = [\n{new_report_js}"
    )

    with open(index_path, "w", encoding="utf-8") as f:
        f.write(content)

    print(f"  [网站] 已更新 index.html，添加 {new_entry['displayDate']} 日报")


# ─── 飞书推送 ───

def get_feishu_token():
    """获取飞书 tenant_access_token"""
    resp = requests.post(
        "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
        json={"app_id": FEISHU_APP_ID, "app_secret": FEISHU_APP_SECRET},
        timeout=10
    )
    return resp.json().get("tenant_access_token", "")


def send_feishu_report(panic, assets, date_str):
    """推送日报摘要到飞书"""
    try:
        token = get_feishu_token()
        if not token:
            print("  [飞书] token获取失败")
            return False

        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

        # 提取关键行情
        spx  = assets.get("SPX", {})
        ndx  = assets.get("NDX", {})
        gold = assets.get("黄金", {})
        brt  = assets.get("布伦特", {})
        vix  = assets.get("VIX", {})
        csi  = assets.get("沪深300", {})

        def fmt(v, prefix="$"):
            if v is None: return "N/A"
            return f"{prefix}{v:,.2f}" if isinstance(v, (int, float)) else str(v)

        panic_level = "高风险" if panic >= 75 else "中高风险" if panic >= 50 else "中等风险" if panic >= 30 else "低风险"
        panic_emoji = "🔴" if panic >= 75 else "🟠" if panic >= 50 else "🟢"

        msg_text = (
            f"[DAILY REPORT] 策略日报 {date_str}\n\n"
            f"核心数据:\n"
            f"  Panic Index: {panic} ({panic_emoji} {panic_level})\n"
            f"  标普500: {fmt(spx.get('price'), '')} {spx.get('change_pct', 0):+.2f}%\n"
            f"  纳斯达克: {fmt(ndx.get('price'), '')} {ndx.get('change_pct', 0):+.2f}%\n"
            f"  黄金: {fmt(gold.get('price'))} {gold.get('change_pct', 0):+.2f}%\n"
            f"  布伦特: {fmt(brt.get('price'))} {brt.get('change_pct', 0):+.2f}%\n"
            f"  VIX: {fmt(vix.get('price'), '')} {vix.get('change_pct', 0):+.2f}%\n"
            f"  沪深300: {fmt(csi.get('price'), '')} {csi.get('change_pct', 0):+.2f}%\n\n"
            f"CPI基准情景: 路径B (CPI 3.0%-3.3%)，地缘降级+美股强势，Panic回落至{panic}区间。\n\n"
            f"完整日报: https://nataliyasadek90-dot.github.io/macro-dashboard/"
        )

        payload = {
            "receive_id": FEISHU_OPEN_ID,
            "msg_type": "text",
            "content": json.dumps({"text": msg_text})
        }

        result = requests.post(
            "https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type=open_id",
            headers=headers, json=payload, timeout=10
        ).json()

        if result.get("code") == 0:
            print(f"  [飞书] 推送成功 (msg_id={result.get('data',{}).get('message_id')})")
            return True
        else:
            print(f"  [飞书] 推送失败: {result.get('msg')}")
            return False

    except Exception as e:
        print(f"  [飞书] 推送异常: {e}")
        return False


# ─── 6. 主函数 ───

def main():
    now_cst = datetime.now(CST)
    print(f"\n{'='*60}")
    print(f"🌍 GeoAnalyst 策略日报 — {now_cst.strftime('%Y-%m-%d %H:%M:%S')} CST")
    print(f"{'='*60}\n")

    # 采集数据
    assets   = collect_market_data()
    headlines = get_news_headlines()

    # 计算指标
    panic = compute_panic_index(assets)

    # 生成MD
    md_content = generate_md(now_cst, assets, headlines)
    filename = f"daily_report_{now_cst.strftime('%Y%m%d')}.md"

    # 更新网站实时行情 JSON
    write_market_live_json(assets)
    filepath = os.path.join(REPORTS_DIR, filename)

    with open(filepath, "w", encoding="utf-8") as f:
        f.write(md_content)
    print(f"\n✅ 日报已生成: {filepath}")

    # 更新网站
    weekday_map = "一二三四五六日"
    update_website_manifest({
        "date":          now_cst.strftime("%Y%m%d"),
        "displayDate":   now_cst.strftime("%Y年%m月%d日"),
        "weekday":       "周" + weekday_map[now_cst.weekday()],
        "summary":       f"自动生成 · Panic Index {panic}",
        "panicIndex":    panic,
        "panicLevel":    "high" if panic >= 75 else "medium" if panic >= 50 else "low",
        "panicLevelText": "高风险" if panic >= 75 else "中高风险" if panic >= 50 else "中等风险",
        "activeFile":    filename,
    })

    # 保存JSON快照（供微信/飞书推送使用）
    snapshot = {
        "date":      now_cst.strftime("%Y-%m-%d"),
        "panic":     panic,
        "assets":    {k: v for k, v in assets.items() if isinstance(v, dict)},
        "md_file":   filename,
        "generated": now_cst.isoformat(),
    }
    snapshot_path = os.path.join(REPORTS_DIR, f"snapshot_{now_cst.strftime('%Y%m%d')}.json")
    with open(snapshot_path, "w", encoding="utf-8") as f:
        json.dump(snapshot, f, ensure_ascii=False, indent=2, default=str)

    print(f"✅ JSON快照已保存: {snapshot_path}")

    # 飞书推送
    send_feishu_report(panic, assets, now_cst.strftime("%Y-%m-%d"))

    print(f"\n📊 今日 Panic Index: {panic}")
    print("🎯 自动生成完成！\n")

    return filepath, md_content


if __name__ == "__main__":
    main()
