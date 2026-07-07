#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
缠论分析数据格式契约（外来模型 ↔ 看板 的导入通道）

看板(chanlun_realtime.html + realtime_server.py)消费的「内部格式」由本模块定义。
任何外部模型只要输出符合 ANALYSIS_FORMAT.md 约定的 JSON，经 import_analysis.py
校验+归一化后写入 chanpy_data_5level.json，看板会在下次请求时自动重载(mtime 比对)。

关键约束：
1. 每级必须含 bars[] 与 chan{}。
2. bars[] 每项: time(字符串), open/high/low/close(数值)。vol/dif/dea/macd 可选(默认0)。
3. chan 必含 bis/segs/zss/bsps 四个数组(可为空); fxs 可选(看板不渲染)。
4. **所有标注的 time / start_time / end_time 必须精确等于某根 bar 的 time 字符串**，
   因为前端用 findIndexByTime 做精确匹配来定位。这是导入能否正确渲染的硬条件。
5. counts(bi_count 等)由本模块按数组长度自动计算，模型无需输出。
"""
import json
import os

KNOWN_LEVELS = ["daily", "30min", "15min", "5min", "1min"]
CHAN_ARRAYS = ("bis", "segs", "zss", "bsps")
BAR_NUM_FIELDS = ("open", "high", "low", "close")
BAR_OPT_FIELDS = ("vol", "dif", "dea", "macd")


def _is_num(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def _err(errors, msg):
    errors.append(msg)


def validate_and_normalize(raw):
    """校验外来模型 JSON，并返回归一化后的内部格式 dict。
    返回 (normalized_dict_or_None, errors_list)
    errors 非空时 normalized 为 None（导入应中止）。
    """
    errors = []
    if not isinstance(raw, dict):
        return None, ["顶层必须是 JSON 对象 {symbol, source, levels}"]

    symbol = raw.get("symbol")
    if not symbol or not isinstance(symbol, str):
        _err(errors, "缺少 symbol(字符串)，如 '588710' 或 '588710.SH'")
    source = raw.get("source")
    if not source or not isinstance(source, str):
        source = "external-model"
    levels = raw.get("levels")
    if not isinstance(levels, dict):
        return None, ["缺少 levels 对象(含 daily/30min/15min/5min/1min)"]

    missing = [lv for lv in KNOWN_LEVELS if lv not in levels]
    if missing:
        _err(errors, "levels 缺少级别: " + ", ".join(missing) + "（看板固定五级）")
    extra = [lv for lv in levels if lv not in KNOWN_LEVELS]
    if extra:
        _err(errors, "levels 含未识别级别(将被忽略): " + ", ".join(extra))

    norm_levels = {}
    for lv in KNOWN_LEVELS:
        if lv not in levels:
            continue
        L = levels[lv]
        if not isinstance(L, dict):
            _err(errors, f"[{lv}] 必须是对象")
            continue
        bars = L.get("bars")
        chan = L.get("chan")
        if not isinstance(bars, list) or not bars:
            _err(errors, f"[{lv}] bars 必须是非空数组")
            continue
        if not isinstance(chan, dict):
            _err(errors, f"[{lv}] chan 必须是对象")
            continue

        # ---- bars ----
        bar_times = []
        norm_bars = []
        for i, b in enumerate(bars):
            if not isinstance(b, dict):
                _err(errors, f"[{lv}] bars[{i}] 不是对象")
                continue
            t = b.get("time")
            if not isinstance(t, str) or not t.strip():
                _err(errors, f"[{lv}] bars[{i}] 缺少 time(字符串)")
                continue
            for f in BAR_NUM_FIELDS:
                if not _is_num(b.get(f)):
                    _err(errors, f"[{lv}] bars[{i}].{f} 必须是数值")
            nb = {"time": t}
            for f in BAR_NUM_FIELDS:
                nb[f] = float(b[f])
            for f in BAR_OPT_FIELDS:
                nb[f] = float(b[f]) if _is_num(b.get(f)) else 0.0
            norm_bars.append(nb)
            bar_times.append(t)
        time_set = set(bar_times)

        # ---- chan arrays ----
        norm_chan = {}
        arr_data = {}
        for arr in CHAN_ARRAYS:
            items = chan.get(arr)
            if items is None:
                _err(errors, f"[{lv}] chan.{arr} 缺失(可为空数组 [])")
                items = []
            if not isinstance(items, list):
                _err(errors, f"[{lv}] chan.{arr} 必须是数组")
                items = []
            norm_items = []
            for j, it in enumerate(items):
                if not isinstance(it, dict):
                    _err(errors, f"[{lv}] {arr}[{j}] 不是对象")
                    continue
                nit, e = _normalize_item(arr, it, lv, j, time_set)
                norm_items.append(nit)
                errors.extend(e)
            arr_data[arr] = norm_items

        fxs = chan.get("fxs")
        if fxs is None:
            fxs = []
        norm_chan = {
            "combined_kline_count": len(norm_bars),
            "bi_count": len(arr_data["bis"]),
            "seg_count": len(arr_data["segs"]),
            "zs_count": len(arr_data["zss"]),
            "bsp_count": len(arr_data["bsps"]),
            "fx_count": len(fxs) if isinstance(fxs, list) else 0,
            "bis": arr_data["bis"],
            "segs": arr_data["segs"],
            "zss": arr_data["zss"],
            "bsps": arr_data["bsps"],
            "fxs": fxs if isinstance(fxs, list) else [],
        }
        norm_levels[lv] = {"bars": norm_bars, "chan": norm_chan}

    if errors:
        return None, errors
    normalized = {"symbol": str(symbol), "source": source, "levels": norm_levels}
    return normalized, []


def _normalize_item(arr, it, lv, j, time_set):
    e = []
    nit = {}
    if arr in ("bis", "segs"):
        d = it.get("direction")
        if d not in ("up", "down"):
            e.append(f"[{lv}] {arr}[{j}].direction 必须是 'up' 或 'down'(收到 {d!r})")
        nit["direction"] = d if d in ("up", "down") else "up"
        nit["is_sure"] = bool(it.get("is_sure", True))
        st, et = it.get("start_time"), it.get("end_time")
        if not isinstance(st, str) or st not in time_set:
            e.append(f"[{lv}] {arr}[{j}].start_time={st!r} 必须等于某根 bar 的 time")
        if not isinstance(et, str) or et not in time_set:
            e.append(f"[{lv}] {arr}[{j}].end_time={et!r} 必须等于某根 bar 的 time")
        nit["start_time"] = st
        nit["end_time"] = et
        nit["start_value"] = float(it["start_value"]) if _is_num(it.get("start_value")) else 0.0
        nit["end_value"] = float(it["end_value"]) if _is_num(it.get("end_value")) else 0.0
        nit["idx"] = int(it["idx"]) if isinstance(it.get("idx"), int) else j
    elif arr == "zss":
        nit["is_sure"] = bool(it.get("is_sure", True))
        st, et = it.get("start_time"), it.get("end_time")
        if not isinstance(st, str) or st not in time_set:
            e.append(f"[{lv}] zss[{j}].start_time={st!r} 必须等于某根 bar 的 time")
        if not isinstance(et, str) or et not in time_set:
            e.append(f"[{lv}] zss[{j}].end_time={et!r} 必须等于某根 bar 的 time")
        nit["start_time"] = st
        nit["end_time"] = et
        nit["low"] = float(it["low"]) if _is_num(it.get("low")) else 0.0
        nit["high"] = float(it["high"]) if _is_num(it.get("high")) else 0.0
        nit["peak_low"] = float(it["peak_low"]) if _is_num(it.get("peak_low")) else nit["low"]
        nit["peak_high"] = float(it["peak_high"]) if _is_num(it.get("peak_high")) else nit["high"]
        nit["idx"] = int(it["idx"]) if isinstance(it.get("idx"), int) else j
    elif arr == "bsps":
        nit["is_buy"] = bool(it.get("is_buy", True))
        ty = it.get("types", it.get("type"))
        if isinstance(ty, str):
            ty = [ty]
        if not isinstance(ty, list) or not ty:
            e.append(f"[{lv}] bsps[{j}].types 必须是非空字符串数组(如 ['1'],['2s'])")
            ty = ["1"]
        nit["types"] = [str(x) for x in ty]
        t = it.get("time")
        if not isinstance(t, str) or t not in time_set:
            e.append(f"[{lv}] bsps[{j}].time={t!r} 必须等于某根 bar 的 time")
        nit["time"] = t
        nit["value"] = float(it["value"]) if _is_num(it.get("value")) else 0.0
        nit["bi_idx"] = int(it["bi_idx"]) if isinstance(it.get("bi_idx"), int) else 0
    return nit, e


def load_foreign(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


if __name__ == "__main__":
    import sys
    p = sys.argv[1] if len(sys.argv) > 1 else "chanpy_data_5level.json"
    data = load_foreign(p)
    norm, errs = validate_and_normalize(data)
    if errs:
        print("校验失败:")
        for x in errs[:40]:
            print("  -", x)
        sys.exit(1)
    print("校验通过，内部格式结构正确。")
