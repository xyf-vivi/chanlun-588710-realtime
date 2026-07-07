#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
校验外来模型的缠论分析 JSON 是否符合看板导入格式契约。

用法:
    python validate_analysis.py <模型输出的.json>

退出码 0=通过, 1=不通过(打印错误)。也可被 import_analysis.py 复用。
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from chan_format import load_foreign, validate_and_normalize


def main():
    if len(sys.argv) < 2:
        print("用法: python validate_analysis.py <模型输出的.json>")
        return 2
    path = sys.argv[1]
    if not os.path.exists(path):
        print(f"文件不存在: {path}")
        return 2
    try:
        data = load_foreign(path)
    except Exception as e:
        print(f"JSON 解析失败: {e}")
        return 2
    norm, errs = validate_and_normalize(data)
    if errs:
        print(f"❌ 校验未通过，共 {len(errs)} 处问题:")
        for x in errs[:50]:
            print("  -", x)
        return 1
    lv = norm["levels"]
    print(f"✅ 校验通过: symbol={norm['symbol']}, source={norm['source']}")
    for k in ("daily", "30min", "15min", "5min", "1min"):
        if k in lv:
            c = lv[k]["chan"]
            print(f"   {k}: bars={len(lv[k]['bars'])} 笔={c['bi_count']} "
                  f"线段={c['seg_count']} 中枢={c['zs_count']} 买卖点={c['bsp_count']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
