# BudgetLens AI：CSV 與 JSON 資料契約（Day 1 對齊稿）

此文件可直接提供給成員 A，作為前後端介面與圖表欄位的共同基準。API 框架尚未定案；以下先定義與框架無關的 JSON body。

## CSV 上傳格式

UTF-8 CSV，一列代表一個部門、費用類別、月份的預算與實際支出。金額須使用同一幣別；`budget` 是該月份該類別的預算額，不是年度預算重複值。

| 欄位 | 必填 | 型別 | 說明 |
|---|---|---|---|
| `department` | 是 | string | 部門名稱，不能空白 |
| `period` | 是 | string | 月份，`YYYY-MM`，例如 `2026-08` |
| `budget` | 是 | number | 該月預算，非負 |
| `actual` | 是 | number | 該月實際支出，非負 |
| `category` | 否 | string | 費用項目；省略或空白時為 `Uncategorized` |

範例：

```csv
department,period,category,budget,actual
研發部,2026-07,雲端服務,120000,128000
研發部,2026-08,授權費,50000,68000
行銷部,2026-08,廣告投放,80000,72000
```

`budget=0` 合法，但使用率與差異百分比無法定義，API 回傳 `null`；負數、非數字、缺少必填欄位或無效月份應回傳 4xx 與可讀錯誤訊息。

## 共用語意

- `usage_rate = actual / budget`，JSON 使用小數比例（`0.8` = 80%）；前端顯示時乘 100。
- `variance = actual - budget`；正數代表超支，負數代表低於預算。
- `variance_pct = variance / budget`，使用小數比例。
- 部門彙總先加總所有月份及類別的 budget/actual，再計算比率；不可平均各列百分比。
- 數值使用 JSON number，缺值使用 `null`，月份使用 `YYYY-MM`。

## 分析結果 JSON（圖表資料）

`POST /api/v1/analyze` 上傳 CSV 後，回傳下列邏輯結構。`status` 供燈號卡片使用；第一版可由超支風險門檻決定，門檻需由 A/B 確認。預測及異常欄位在模型完成前可回傳空陣列/null，欄位名稱先固定。

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
      "forecast": {
        "year_end_amount": 294000,
        "ci95": {"lower": 250000, "upper": 338000},
        "overrun_probability": 0.87
      },
      "cause_inference": {
        "observed_outcome": "超支",
        "baseline_overrun_probability": 0.455,
        "primary_cause": {
          "cause": "當前進度",
          "state": "提前",
          "reason": "累計支出進度快於年度時間進度",
          "recommendation": "檢查剩餘預算與未來承諾支出，必要時調整支出節奏。",
          "prior_probability": 0.5,
          "posterior_probability": 0.7473,
          "posterior_probability_lift": 0.2473,
          "overrun_probability_given_state": 0.68,
          "risk_probability_lift": 0.225
        },
        "ranked_causes": [
          {
            "cause": "當前進度",
            "state": "提前",
            "reason": "累計支出進度快於年度時間進度",
            "recommendation": "檢查剩餘預算與未來承諾支出，必要時調整支出節奏。",
            "prior_probability": 0.5,
            "posterior_probability": 0.7473,
            "posterior_probability_lift": 0.2473,
            "overrun_probability_given_state": 0.68,
            "risk_probability_lift": 0.225
          },
          {
            "cause": "歷史速率",
            "state": "快",
            "reason": "去年實際支出超過預算 5% 以上",
            "recommendation": "回顧去年超支項目，並確認今年是否需要調高相關預算。",
            "prior_probability": 0.25,
            "posterior_probability": 0.3407,
            "posterior_probability_lift": 0.0907,
            "overrun_probability_given_state": 0.62,
            "risk_probability_lift": 0.165
          },
          {
            "cause": "季節因素",
            "state": "旺季",
            "reason": "季節模型判定目前屬於旺季",
            "recommendation": "將旺季支出納入後續月份預測與預算配置。",
            "prior_probability": 0.3,
            "posterior_probability": 0.3692,
            "posterior_probability_lift": 0.0692,
            "overrun_probability_given_state": 0.56,
            "risk_probability_lift": 0.105
          }
        ]
      },
      "diagnostics": [
        {
          "period": "2026-08",
          "category": "授權費",
          "metric": "actual",
          "z_score": 2.4,
          "reason": "支出高於歷史平均",
          "recommendation": "檢查授權席次與續約項目"
        }
      ],
      "monthly": [
        {"period": "2026-07", "budget": 120000, "actual": 128000, "usage_rate": 1.0667}
      ]
    }
  ]
}
```

燈號列舉值：`green | yellow | red`。建議使用超支機率作為燈號依據；尚無預測時可暫以使用率/差異規則產生暫定狀態，並在 UI 標示為暫定。

## 模擬預算 JSON

`POST /api/v1/simulate` 以部門為粒度傳入新預算。前端拖曳拉桿時送出完整的部門預算覆寫值；回傳資料應沿用分析回應的 `departments` 欄位，方便直接更新全頁圖表與卡片。

```json
{
  "analysis_id": "由分析 API 回傳的識別碼",
  "overrides": [{"department": "研發部", "budget": 200000}]
}
```

模擬 API 回傳：`{"analysis_id":"...","departments":[...]}`。目標互動延遲：P95 小於 500 ms；可用前端 debounce 避免拉桿每個像素都呼叫。

## 錯誤回應

```json
{"error":{"code":"INVALID_CSV","message":"period must use YYYY-MM format","details":[{"row":3,"field":"period"}]}}
```

## 尚待 A/B 確認

1. 幣別與範例資料目前暫定 TWD。
2. `budget` 定義為月預算；若來源資料只有年度預算，需另加年度預算欄位或明確的分攤方式。
3. `status` 燈號門檻、年度截止/預測目標期間與空月份處理規則。
4. CSV 傳輸採 multipart/form-data；API 路徑與框架（FastAPI/Flask）待整合時決定。
