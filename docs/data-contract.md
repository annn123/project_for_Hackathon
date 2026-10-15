# BudgetLens AI：CSV 與 JSON 資料契約

此文件可直接提供給成員 A，作為前後端介面與圖表欄位的共同基準。目前 API 使用 FastAPI；端點細節見 `docs/api-forecast-and-simulation.md`。

## CSV 上傳格式

UTF-8 CSV，一列代表一個部門、費用類別、月份的預算與實際支出。金額須使用同一幣別；`budget` 是該月份該類別的預算額，不是年度預算重複值。

| 欄位 | 必填 | 型別 | 說明 |
|---|---|---|---|
| `department` | 是 | string | 部門名稱，不能空白 |
| `period` | 是 | string | 月份，`YYYY-MM`，例如 `2026-08` |
| `budget` | 是 | number | 該月預算，非負 |
| `actual` | 是 | number 或空白 | 該月實際支出，非負。未來／尚未回報的整月可留白；同一部門月份的所有類別須一致留白，且實支月份之後不得再出現有實支的月份。空白代表未知，不代表 0。 |
| `category` | 否 | string | 費用項目；省略或空白時為 `Uncategorized` |

範例：

```csv
department,period,category,budget,actual
研發部,2026-07,雲端服務,120000,128000
研發部,2026-08,授權費,50000,68000
行銷部,2026-08,廣告投放,80000,72000
```

`budget=0` 合法，但使用率與差異百分比無法定義，API 回傳 `null`；負數、非數字（`actual` 空白除外）、缺少必填欄位或無效月份應回傳 4xx 與可讀錯誤訊息。未來月份仍列入預算與月度圖表；只有有實支的月份用於 YTD 指標及年底預測。若預算涵蓋完整 1–12 月，年度預算基準使用 12 個月預算合計。

## 共用語意

- `usage_rate = actual / budget`，JSON 使用小數比例（`0.8` = 80%）；前端顯示時乘 100。
- `variance = actual - budget`；正數代表超支，負數代表低於預算。
- `variance_pct = variance / budget`，使用小數比例。
- 分析 API 以最新年度的月份及類別資料加總部門 budget/actual，再計算比率；不可平均各列百分比。`period_count` 和 `monthly` 也只包含最新年度。
- 數值使用 JSON number，缺值使用 `null`，月份使用 `YYYY-MM`。

## 分析結果 JSON（圖表資料）

`POST /api/v1/analyze` 上傳 CSV 後，回傳下列結構。`status` 供燈號卡片使用，目前暫以預測超支機率門檻決定。`diagnostics` 目前固定為空陣列；Z-score 診斷尚未接入 API。`model_evidence` 列出貝氏網路實際採用的分類證據。

預算不足與執行落後的警示邏輯目前先記錄於 `docs/model-assumptions-and-alert-rules.md`，尚未加入 API JSON；門檻確認後再新增獨立 `alerts` 欄位。CSV 未從 1 月連續涵蓋到最新月份時，`model_evidence` 不會包含 `當前進度`，以免把觀測筆數誤當作日曆經過月數。

```json
{
  "schema_version": "1.0",
  "currency": "TWD",
  "departments": [
    {
      "department": "研發部",
      "status": "yellow",
      "budget": 170000,
      "actual": 196000,
      "variance": 26000,
      "usage_rate": 1.1529,
      "variance_pct": 0.1529,
      "period_count": 2,
      "latest_period": "2026-08",
      "annual_budget": 1020000,
      "forecast": {
        "year_end_amount": 1176000,
        "ci95": {"lower": 900000, "upper": 1452000},
        "overrun_probability": 0.76,
        "bayesian_overrun_probability": 0.58,
        "method": "ytd_plus_remaining_run_rate",
        "periods_used": 8
      },
      "model_evidence": {
        "歷史速率": "快",
        "當前進度": "提前",
        "季節因素": "淡季"
      },
      "diagnostics": [],
      "monthly": [
        {"period": "2026-07", "budget": 120000, "actual": 128000, "usage_rate": 1.0667}
      ]
    }
  ]
}
```

燈號列舉值：`green | yellow | red`。目前門檻是綠燈 `<33%`、黃燈 `33%–67%`、紅燈 `>=67%`。若 run-rate 超支機率因月份不足回傳 `null`，燈號改用貝氏網路機率。上述機率定義與估算假設見 `docs/api-forecast-and-simulation.md`。

## 模擬預算 JSON

`POST /api/v1/simulate` 以部門為粒度傳入新預算。前端拖曳拉桿時送出完整的部門預算覆寫值；回傳資料應沿用分析回應的 `departments` 欄位，方便直接更新全頁圖表與卡片。

```json
{
  "analysis_id": "由分析 API 回傳的識別碼",
  "overrides": [{"department": "研發部", "annual_budget": 1200000}]
}
```

模擬 API 回傳：`{"analysis_id":"...","departments":[...]}`。目標互動延遲：P95 小於 500 ms；可用前端 debounce 避免拉桿每個像素都呼叫。

## 錯誤回應

```json
{"error":{"code":"INVALID_CSV","message":"period must use YYYY-MM format","details":[{"row":3,"field":"period"}]}}
```

## 尚待 A/B 確認

1. 幣別與範例資料目前暫定 TWD。
2. CSV `budget` 定義為月預算；模擬時的 `annual_budget` 是年度預算覆寫值。
3. 燈號門檻、年度目標期間與缺月規則目前為 MVP 暫定值，請 A/B 與指導教授確認。
4. API 已採 multipart/form-data 與 FastAPI；瀏覽器前端來源請用 `BUDGETLENS_CORS_ORIGINS` 設定。
