#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
588710 缠论实时看板 —— 本地后端服务
- 加载 chan.py 算好的 5 级别缠论结构（缓存 JSON，文件变更自动重载）
- 后台线程每 3 秒从新浪财经抓 588710 实时报价（现价/涨跌幅/开高低/时间）
- /api/snapshot?full=1 返回完整结构 + 实时价 + 操作建议
- /api/snapshot        返回轻量（实时价 + 操作建议 + live_price），供前端轮询
- /                    返回实时看板页面
纯标准库，无第三方依赖（akshare 被沙箱代理拦截，故实时价走新浪）。
"""
import json
import os
import threading
import time
import datetime
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

BASE = os.path.dirname(os.path.abspath(__file__))
DATA_JSON = os.path.join(BASE, "chanpy_data_5level.json")
HTML_FILE = os.path.join(BASE, "chanlun_realtime.html")
SYMBOL = "588710"
SINA_URL = "https://hq.sinajs.cn/list=sh588710"
PORT = 8899

# ---------------- 数据缓存（缠论结构，文件变更自动重载） ----------------
_data_cache = {"data": None, "mtime": 0.0, "freshness": ""}
_data_lock = threading.Lock()


def load_data():
    global _data_cache
    try:
        mtime = os.path.getmtime(DATA_JSON)
        with _data_lock:
            if _data_cache["data"] is None or mtime != _data_cache["mtime"]:
                with open(DATA_JSON, encoding="utf-8") as f:
                    d = json.load(f)
                # 记录全量刷新时间（各级别最后一根 bar 的时间）
                fresh = ""
                lv = d.get("levels", {})
                for k in ("daily", "30min", "15min", "5min", "1min"):
                    if k in lv and lv[k]["bars"]:
                        fresh = lv[k]["bars"][-1]["time"][:10]
                _data_cache["data"] = d
                _data_cache["mtime"] = mtime
                _data_cache["freshness"] = fresh
    except Exception as e:
        print("[load_data] error:", e)
    return _data_cache["data"]


# ---------------- 实时报价缓存（后台线程刷新） ----------------
_quote_cache = {"quote": None, "ts": 0.0}
_quote_lock = threading.Lock()


def fetch_quote():
    try:
        req = urllib.request.Request(
            SINA_URL,
            headers={"Referer": "https://finance.sina.com.cn", "User-Agent": "Mozilla/5.0"},
        )
        raw = urllib.request.urlopen(req, timeout=8).read().decode("gbk", "ignore")
        # var hq_str_sh588710="名称,今开,昨收,现价,最高,最低,买一,卖一,成交量,成交额,...,日期,时间,状态";
        s = raw.split('"')[1]
        p = s.split(",")
        name = p[0]
        open_ = float(p[1])
        prev = float(p[2])
        price = float(p[3])
        high = float(p[4])
        low = float(p[5])
        date = p[-3]
        tm = p[-2]
        pct = (price - prev) / prev * 100 if prev else 0.0
        q = {
            "name": name,
            "open": round(open_, 3),
            "prev_close": round(prev, 3),
            "price": round(price, 3),
            "high": round(high, 3),
            "low": round(low, 3),
            "date": date,
            "time": tm,
            "pct": round(pct, 2),
        }
        with _quote_lock:
            _quote_cache["quote"] = q
            _quote_cache["ts"] = time.time()
    except Exception as e:
        # 抓不到就保留上一次的结果
        pass


def get_quote():
    with _quote_lock:
        return _quote_cache["quote"], _quote_cache["ts"]


def market_status():
    try:
        now = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8)))
    except Exception:
        now = datetime.datetime.now()
    wd = now.weekday()
    if wd >= 5:
        return "休市"
    t = now.hour * 60 + now.minute
    if t < 9 * 60 + 30:
        return "盘前"
    if 9 * 60 + 30 <= t <= 11 * 60 + 30:
        return "盘中"
    if 11 * 60 + 30 < t < 13 * 60:
        return "午间休市"
    if 13 * 60 <= t <= 15 * 60:
        return "盘中"
    return "已收盘"


# ---------------- 下一步操作建议（基于缠论信号降维成白话） ----------------
def level_signal(lv):
    bars = lv["bars"]
    ch = lv["chan"]
    last = bars[-1]["close"]
    bis = ch.get("bis") or []
    zss = ch.get("zss") or []
    bsps = ch.get("bsps") or []
    last_bi = bis[-1] if bis else None
    last_zs = zss[-1] if zss else None
    direction = last_bi["direction"] if last_bi else "side"
    trend_up = trend_down = False
    if len(bis) >= 2:
        a, b = bis[-2], bis[-1]
        trend_up = (b["end_value"] > a["end_value"]) and (b["start_value"] > a["start_value"])
        trend_down = (b["end_value"] < a["end_value"]) and (b["start_value"] < a["start_value"])
    pos = "none"
    if last_zs:
        if last > last_zs["high"]:
            pos = "above"
        elif last < last_zs["low"]:
            pos = "below"
        else:
            pos = "inside"
    recent = None
    if bsps:
        recent = dict(bsps[-1])
        cur_idx = last_bi["idx"] if last_bi else 0
        recent["active"] = recent.get("bi_idx", 0) >= cur_idx - 3
    return {
        "last": last,
        "direction": direction,
        "trend_up": trend_up,
        "trend_down": trend_down,
        "pos": pos,
        "zs": (last_zs["low"], last_zs["high"]) if last_zs else None,
        "recent": recent,
    }


def bias_of(grp):
    up = any(x["trend_up"] or (x["direction"] == "up" and x["pos"] in ("above", "inside")) for x in grp)
    down = any(x["trend_down"] or (x["direction"] == "down" and x["pos"] in ("below", "inside")) for x in grp)
    if up and not down:
        return "偏多"
    if down and not up:
        return "偏空"
    return "震荡"


def compute_suggestion(data, quote):
    levels = data.get("levels", {})
    order = ["daily", "30min", "15min", "5min", "1min"]
    sigs = {lv: level_signal(levels[lv]) for lv in order if lv in levels}
    big = [sigs[k] for k in ("daily", "30min") if k in sigs]
    small = [sigs[k] for k in ("5min", "1min") if k in sigs]

    big_bias = bias_of(big) if big else "震荡"
    small_bias = bias_of(small) if small else "震荡"

    small_bsp = None
    for x in small:
        if x["recent"] and x["recent"].get("active"):
            small_bsp = x["recent"]
            break
    big_bsp = None
    for x in big:
        if x["recent"] and x["recent"].get("active"):
            big_bsp = x["recent"]
            break

    pos_word = {"above": "中枢上方（强势区）", "below": "中枢下方（弱势区）", "inside": "中枢内部（震荡区）", "none": "暂无中枢参考"}
    bullets = []

    # 1) 大级别方向
    b0 = big[0] if big else None
    if b0:
        bullets.append(f"大级别（日线/30分）整体{big_bias}，现价落在{b0['pos'] and pos_word.get(b0['pos'],'')}。")
    else:
        bullets.append(f"大级别方向：{big_bias}。")

    # 2) 小级别信号
    if small_bsp:
        kind = "买点" if small_bsp.get("is_buy") else "卖点"
        word = "低吸" if small_bsp.get("is_buy") else "减仓/止盈"
        bullets.append(f"小级别（5分/1分）刚出现{kind}信号 {','.join(small_bsp.get('types', []))}（{small_bsp.get('value'):.3f}），是短线{word}的参考位置。")
    else:
        bullets.append("小级别（5分/1分）暂无新的买卖点，继续观察，不急着动。")

    # 3) 现价与涨跌
    if quote:
        pct = quote.get("pct", 0)
        arrow = "涨" if pct > 0 else ("跌" if pct < 0 else "平")
        bullets.append(f"现价 ¥{quote.get('price'):.3f}（{arrow} {pct:+.2f}%），数据为{big_bias}背景下的实时报价。")

    # 4) 动作建议（多级别联立）
    action = ""
    if big_bias == "偏多" and small_bsp and small_bsp.get("is_buy"):
        action = "大方向没问题，小级别回踩给了上车机会——可分批低吸，别一把梭。"
    elif big_bias == "偏多" and small_bsp and not small_bsp.get("is_buy"):
        action = "大方向偏多但短线冲高出现卖点，仓位重的先落袋一部分，留底仓跟随。"
    elif big_bias == "偏多":
        action = "还在上涨段、没到卖点，拿住即可；但别追高加仓。"
    elif big_bias == "偏空" and small_bsp and small_bsp.get("is_buy"):
        action = "大级别还在探底，小级别买点只当反弹看，轻仓试探，破位就走。"
    elif big_bias == "偏空" and small_bsp and not small_bsp.get("is_buy"):
        action = "顺势减仓，别接飞刀，等止跌信号再说。"
    elif big_bias == "偏空":
        action = "弱势震荡，观望为主，等出现止跌买点再考虑。"
    else:
        action = "多空胶着，箱体里高抛低吸或干脆不动，等方向明朗。"

    bullets.append("操作参考：" + action)

    risk = "缠论是技术参考，不是买卖指令。ETF 有波动风险，以上仅为基于历史结构的个人研究记录，不构成投资建议。"

    return {
        "bias": big_bias,
        "small_bias": small_bias,
        "action": action,
        "bullets": bullets,
        "risk": risk,
    }


def build_snapshot(full=False):
    data = load_data()
    quote, qts = get_quote()
    status = market_status()
    freshness = _data_cache["freshness"]
    suggestion = compute_suggestion(data, quote) if data else {"bias": "未知", "action": "", "bullets": ["暂无数据"], "risk": ""}
    out = {
        "updated_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "market_status": status,
        "quote": quote,
        "data_freshness": freshness,
        "suggestion": suggestion,
        "live_price": quote["price"] if quote else None,
    }
    if full and data:
        out["levels"] = data["levels"]
        out["symbol"] = data.get("symbol", SYMBOL)
        out["source"] = data.get("source", "Vespa314/chan.py (live)")
    return out


# ---------------- HTTP 处理 ----------------
class Handler(BaseHTTPRequestHandler):
    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False)
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        if path in ("/", "/index.html"):
            if os.path.exists(HTML_FILE):
                with open(HTML_FILE, "rb") as f:
                    self._send(200, f.read(), "text/html; charset=utf-8")
            else:
                self._send(404, "看板页面未生成，请先运行构建脚本")
            return
        if path == "/api/snapshot":
            full = "full" in parsed.query
            try:
                self._send(200, build_snapshot(full=full))
            except Exception as e:
                self._send(500, {"error": str(e)})
            return
        self._send(404, {"error": "not found"})

    def log_message(self, *args):
        pass  # 静默


def quote_loop():
    while True:
        fetch_quote()
        time.sleep(3)


def main():
    # 预热一次报价
    fetch_quote()
    t = threading.Thread(target=quote_loop, daemon=True)
    t.start()
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print(f"[OK] 实时看板服务已启动: http://127.0.0.1:{PORT}/")
    print(f"[OK] 数据文件: {DATA_JSON}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.shutdown()


if __name__ == "__main__":
    main()
