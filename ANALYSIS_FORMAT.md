# 缠论分析数据格式契约（外来模型 → 看板导入）

本看板（`chanlun_realtime.html` + `realtime_server.py`）消费的缠论数据有固定结构。
任何**其他模型 / 算法 / LLM** 只要按本契约输出 JSON，即可经
`validate_analysis.py` 校验、`import_analysis.py` 导入，被看板直接渲染，无需改前端。

> 一句话：你的模型输出一份 JSON → 跑 `python import_analysis.py 你的输出.json` →
> 看板在下次刷新时自动显示。可参考仓库内 `sample_external_analysis.json`。

---

## 顶层结构

```json
{
  "symbol": "588710",
  "source": "你的模型名/方法名",
  "levels": { "daily": {...}, "30min": {...}, "15min": {...}, "5min": {...}, "1min": {...} }
}
```

| 字段 | 必填 | 说明 |
|------|------|------|
| `symbol` | 是 | 字符串，如 `"588710"` 或 `"588710.SH"` |
| `source` | 否 | 字符串，标记数据来源；缺省填 `"external-model"` |
| `levels` | 是 | **必须包含全部 5 个级别**（看板固定五级，缺一级前端会报错） |

级别键固定为：`daily` / `30min` / `15min` / `5min` / `1min`。

---

## 每个级别 `levels[<级别>]`

```json
{
  "bars": [ ... ],
  "chan": { "bis": [...], "segs": [...], "zss": [...], "bsps": [...] }
}
```

### 1. `bars[]` —— K 线（画蜡烛用，必填非空）

每根 bar：

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `time` | 字符串 | 是 | **daily 用 `YYYY-MM-DD`；其余级别用 `YYYY-MM-DD HH:MM`** |
| `open` | 数值 | 是 | |
| `high` | 数值 | 是 | |
| `low` | 数值 | 是 | |
| `close` | 数值 | 是 | |
| `vol` / `dif` / `dea` / `macd` | 数值 | 否 | 缺省按 0 处理（成交量/MACD 图层为空） |

### 2. `chan` —— 缠论标注

四个数组**必须存在**（可为空 `[]`）；`fxs`（分型）可选（看板不渲染，可不给）。

#### `bis[]` 笔 / `segs[]` 线段（同结构）

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `direction` | 字符串 | 是 | 只能是 `"up"` 或 `"down"` |
| `is_sure` | 布尔 | 否 | 是否确认笔/线段，缺省 `true` |
| `start_time` | 字符串 | 是 | **必须精确等于 `bars` 中某根 `time`** |
| `end_time` | 字符串 | 是 | **必须精确等于 `bars` 中某根 `time`** |
| `start_value` | 数值 | 是 | 起点价位 |
| `end_value` | 数值 | 是 | 终点价位 |
| `idx` | 整数 | 否 | 序号，缺省按顺序生成 |

#### `zss[]` 中枢

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `is_sure` | 布尔 | 否 | 缺省 `true` |
| `start_time` / `end_time` | 字符串 | 是 | 必须对应 `bars` 中存在的 `time` |
| `low` / `high` | 数值 | 是 | 中枢上下沿 |
| `peak_low` / `peak_high` | 数值 | 否 | 中枢波动高低，缺省取 `low`/`high` |
| `idx` | 整数 | 否 | 缺省按顺序生成 |

#### `bsps[]` 买卖点

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `is_buy` | 布尔 | 是 | `true`=买点，`false`=卖点 |
| `types` | 字符串数组 | 是 | 如 `["1"]`、`["2s"]`、`["3a"]`、`["3b"]`、`["1p"]`、`["2s"]` |
| `time` | 字符串 | 是 | **必须精确等于 `bars` 中某根 `time`** |
| `value` | 数值 | 是 | 买卖点价位 |
| `bi_idx` | 整数 | 否 | 关联笔序号，缺省 0 |

---

## ⚠️ 最关键的硬规则：时间必须对齐

前端用 `findIndexByTime` 对标注的 `time` / `start_time` / `end_time` 做**精确字符串匹配**
来定位到 K 线。所以：

- 每一条 `bi`/`seg`/`zs`/`bsp` 里出现的时间，**必须原样等于 `bars` 里某根的 `time`**。
- 大小写、空格、格式（`YYYY-MM-DD` vs `YYYY-MM-DD HH:MM`）必须完全一致。
- 时间对不上的标注会被 `validate_analysis.py` 报错并指出。

---

## 计数字段（`bi_count` 等）

`combined_kline_count` / `bi_count` / `seg_count` / `zs_count` / `bsp_count` / `fx_count`
**你的模型不用输出**——`import_analysis.py` 会按数组长度自动计算并填好。

---

## 校验与导入

```bash
# 1) 先校验（不改动任何文件，打印错误或结构摘要）
python validate_analysis.py 你的输出.json

# 2) 校验通过后导入（备份旧数据 + 写入 chanpy_data_5level.json）
python import_analysis.py 你的输出.json
#   想写到别处: python import_analysis.py 你的输出.json --out 其他.json
```

导入后 `realtime_server.py` 按文件修改时间自动重载，**无需重启**，浏览器刷新即可见。

不合规则 `import_analysis.py` 会拒绝导入并列出所有问题（例如某条买卖点 time 不在 bars 中）。
