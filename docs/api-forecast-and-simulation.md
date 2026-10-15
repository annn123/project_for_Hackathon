# API、年底預測與預算模擬

後端使用 FastAPI。收到 CSV 後建立分析快照，提供部門月資料、預估年底金額、95% 區間與可供拉桿重算的年度預算覆寫。互動文件由 FastAPI 提供於 `/docs`。

## 啟動

```powershell
python -m pip install -e .
uvicorn budgetlens.api:app --reload
```

## 端點

- `GET /api/v1/health`：健康檢查。
- `POST /api/v1/analyze`：multipart/form-data，上傳欄位名稱為 `file`，`currency` 選填（預設 TWD）。回應含 `analysis_id` 與所有部門資料。
- `POST /api/v1/simulate`：傳入分析識別碼與新的年度預算；回傳同一份完整部門結構，前端可以整頁更新。

模擬請求範例：

```json
{
  "analysis_id": "分析端點回傳的識別碼",
  "overrides": [
    {"department": "研發部", "annual_budget": 1200000}
  ]
}
```

`annual_budget` 是年度預算覆寫值；CSV 的 `budget` 仍表示每一列對應月份與類別的月預算。可在拉桿拖曳時使用 debounce，再送出最新年度預算值。

## 預測方法與解讀

每個部門只用最新年度資料。若資料從 1 月連續覆蓋到最新月份，年底點估計為年初至今實際支出加上剩餘月份的平均月支出預估；否則以已提供月份的平均實際支出年化。`forecast.method` 會標示所用方法。

CSV 可列出完整年度預算，並將尚未發生／尚未回報月份的 `actual` 留白。空白月份必須是每個部門資料尾端的連續月份，同一月份所有類別都要一起留白；空白不代表支出為 0。預測與 YTD 指標只使用有實際支出的月份；月度回應仍會包含未來預算，該月 `actual` 和 `usage_rate` 為 `null`。若預算列完整涵蓋 1–12 月，年度預算基準使用全年月預算加總。

`ci95` 和 `overrun_probability` 使用 Student-t 分布與樣本月支出波動估計。完整年初至今資料時，區間估計剩餘月支出；資料月份不完整時，區間表示年化平均支出的不確定性。少於兩個月份無法估計變異，兩者回傳 `null`。這是 MVP 的 run-rate 假設，非經時間序列回測驗證的正式模型。

`forecast.overrun_probability` 是 run-rate 分布超過年度預算的機率，會隨拉桿連續改變；`forecast.bayesian_overrun_probability` 是現有貝氏網路根據分類證據計算的結果，可能只在證據狀態跨越分類門檻時改變。兩者的統計意義不同，不應混為同一數值。

燈號門檻暫定為綠燈 `<33%`、黃燈 `33%–67%`、紅燈 `>=67%`；教授或 A/B 確認後再調整。若 run-rate 機率因資料不足是 `null`，燈號暫用貝氏網路結果。

## 推論快取與回應時間

貝氏網路只會出現有限種離散證據組合，因此 `posterior_overrun_probability()` 會依證據及先驗參數快取已算出的後驗機率，避免 Slider 每次變動都重新建立網路並執行 Variable Elimination。快取上限為 512 組；先驗不同或證據分類改變時會重新計算並保存。

Windows、Python 3.14.8、3 部門 × 12 個月的 `sample_budget.csv` 本機微基準（2026-10-11）：優化前 `_build_response` 30 次分析的中位數 39.59 ms、P95 59.81 ms；優化後中位數 26.07 ms、P95 37.19 ms。交替改動研發部年度預算的 30 次模擬，中位數 33.34 ms、P95 48.12 ms。計時包含 CSV 已載入後的後端分析／重算函式，不包含 HTTP、網路、JSON 傳輸或前端繪圖；不代表端到端 SLA。此樣本的狀態組合少，尚須以較多部門和實際前端拖桿情境再量測。

## MVP 限制

- 分析快照暫存在 API 程序記憶體，最多保留 50 筆；重啟服務或切換 worker 後需重新上傳 CSV。
- 月度資料不完整時使用可用月份年化，必須在 UI 或簡報標示該估算方法。
- `diagnostics` 目前為空陣列，Z-score 異常偵測尚待實作。
- CORS 預設開放 localhost 的 3000、5173、5500、8501 埠；其他前端來源可用 `BUDGETLENS_CORS_ORIGINS` 以逗號分隔設定。
