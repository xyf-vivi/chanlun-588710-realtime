#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
把其他模型输出的缠论分析 JSON 导入看板。

流程: 校验 → 归一化为看板内部格式 → 备份旧 chanpy_data_5level.json → 写入。
realtime_server.py 按文件 mtime 比对缓存，写入后下次请求自动重载，无需重启。

用法:
    python import_analysis.py <模型输出的.json> [--out chanpy_data_5level.json]

示例:
    python import_analysis.py my_model_output.json
"""
import sys
import os
import json
import shutil
import datetime
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from chan_format import load_foreign, validate_and_normalize

BASE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_OUT = os.path.join(BASE, "chanpy_data_5level.json")


def main():
    args = sys.argv[1:]
    if not args:
        print("用法: python import_analysis.py <模型输出的.json> [--out chanpy_data_5level.json]")
        return 2
    src = args[0]
    out = DEFAULT_OUT
    if "--out" in args:
        i = args.index("--out")
        if i + 1 < len(args):
            out = args[i + 1]
    if not os.path.exists(src):
        print(f"源文件不存在: {src}")
        return 2

    data = load_foreign(src)
    norm, errs = validate_and_normalize(data)
    if errs:
        print(f"❌ 校验未通过，已中止导入（未改动原数据）。共 {len(errs)} 处问题:")
        for x in errs[:50]:
            print("  -", x)
        return 1

    # 备份旧文件
    if os.path.exists(out):
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        bak = out + f".bak_{ts}"
        shutil.copy2(out, bak)
        print(f"已备份旧数据: {bak}")

    with open(out, "w", encoding="utf-8") as f:
        json.dump(norm, f, ensure_ascii=False, indent=1)
    print(f"✅ 已导入并写入: {out}")
    print(f"   symbol={norm['symbol']} source={norm['source']}")
    for k in ("daily", "30min", "15min", "5min", "1min"):
        if k in norm["levels"]:
            c = norm["levels"][k]["chan"]
            print(f"   {k}: 笔={c['bi_count']} 线段={c['seg_count']} 中枢={c['zs_count']} 买卖点={c['bsp_count']}")
    print("看板将在下次请求时自动重载（无需重启 realtime_server.py）。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
