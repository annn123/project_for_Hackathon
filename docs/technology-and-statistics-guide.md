# BudgetLens AI 技術與統計知識總覽

這份文件整理專案使用的技術、統計概念、公式、資料流與目前實作狀態，供團隊開發、簡報和展示時查閱。細節契約請參考 [`data-contract.md`](data-contract.md)、[`bayesian-model.md`](bayesian-model.md)、[`model-assumptions-and-alert-rules.md`](model-assumptions-and-alert-rules.md) 與 [`api-forecast-and-simulation.md`](api-forecast-and-simulation.md)。

## 1. 系統資料流程

1. 使用者上傳 UTF-8 CSV，欄位包含部門、月份、月預算、實際支出及可選費用類別。
2. pandas 驗證欄位與數值、清理月份格式，彙整最新年度的部門月資料。
3. 後端計算預算使用率、差異、年底預測與貝氏網路風險。
4. FastAPI 將結果以 JSON 回傳給前端；前端可將年度預算覆寫值送到模擬端點，重新計算預測。
5. 部門卡片、圖表、燈號和風險說明由前端呈現。正式前端框架仍由 A 確認。

## 2. 使用技術

| 技術 | 用途 | 專案狀態 |
|---|---|---|
| Python | 後端與統計計算 | 已使用；使用者開發環境為 Python 3.14.8。 |
| pandas | CSV 讀取、驗證、清理、月度及部門彙總 | 已實作於 `src/budgetlens/data.py`。 |
| pgmpy | 離散貝氏網路、Variable Elimination 後驗推論 | 已實作於 `src/budgetlens/bayesian.py`；先驗與 CPT 仍屬暫定專家假設。 |
| SciPy | Student-t 分布的區間與尾端機率計算 | 已用於年底 run-rate 預測。 |
| FastAPI | CSV 分析、What-If 模擬及健康檢查 API | 已實作於 `src/budgetlens/api.py`。 |
| Uvicorn | 本機啟動 FastAPI 服務 | 已使用。 |
| Pydantic | 驗證模擬 API 的 JSON 請求欄位 | 已使用。 |
| python-multipart | 接收瀏覽器 multipart/form-data CSV 上傳 | 已使用。 |
| Chart.js、Plotly、Streamlit | 前端儀表板與互動圖表 | 尚未定案；由 A 確認後再整合。 |

## 3. 預算描述統計

所有比率以小數存入 JSON，顯示百分比時乘以 100。

- 預算使用率：`usage_rate = actual / budget`
- 金額差異：`variance = actual - budget`
- 差異百分比：`variance_pct = (actual - budget) / budget`
- 部門加總後再計算比率；不可先算每列百分比再取平均。
- 預算為零時，使用率與差異百分比沒有定義，API 使用 `null`。

例如預算 100、實際支出 90，使用率是 0.90（90%），差異是 -10，差異百分比是 -0.10（低於預算 10%）。

## 4. 貝氏統計與風險推論

### 先驗、概似與後驗

- **先驗機率（prior）**：觀察目前資料前，模型對某狀態的機率。本專案的根節點暫定先驗為歷史速率快／持平／慢 `0.25/0.50/0.25`、進度提前／落後 `0.50/0.50`、季節旺／淡 `0.30/0.70`。
- **概似（likelihood）**：在某個原因狀態下看到結果的機率，例如 `P(超支 | 當前進度=提前)`。本專案以條件機率表（CPT）提供這類條件機率。
- **後驗機率（posterior）**：觀察證據後更新的風險估計。一般形式為 `P(H | E) = P(E | H) × P(H) / P(E)`；本專案透過貝氏網路邊際化未觀測原因，計算 `P(超支 | 已提供證據)`。

### 網路節點與暫定 CPT

模型圖為：

```text
歷史速率 ─┐
當前進度 ─┼─> 超支風險
季節因素 ─┘
```

CPT 使用規則式加分：

`P(超支 | h, p, s) = clip((5 + 歷史分數 + 進度分數 + 季節分數) / 100, 0, 1)`

- 歷史速率快／持平／慢：加 30／12／0 分。
- 當前進度提前／落後：加 45／0 分。
- 季節旺／淡：加 15／0 分。
- 基礎分為 5 分。

例如「歷史快、進度提前、旺季」的 CPT 值是 95%。目前參數沒有以企業歷史資料估計或校準，機率是模型假設下的示範輸出，不代表已驗證的企業超支機率。

### 離散狀態判定

- 歷史速率：去年實際／預算大於 1.05 為快，小於 0.95 為慢，其餘持平。
- 當前進度：YTD 使用率高於已過月份比例為提前，否則落後。資料需從 1 月連續涵蓋到最新月才提供此項證據。
- 季節因素：以最新月支出相對觀測月平均的倍率，結合暫定產業倍率 1.0、權重 0.4/0.6 與 1.5 門檻判定旺季或淡季。

### 反向推論與原因說明

反向推論是計算觀察到超支後各原因狀態的機率 `P(原因狀態 | 超支)`，並比較不同狀態下的條件超支率與基準超支率。這提供模型相關性解釋，不證明原因造成超支。`infer_overrun_causes()` 已在模型層實作，API 尚未提供部門層級的原因診斷。

## 5. 年底預測、Student-t 與 95% 區間

令 `n` 為已觀測月份數，`ȳ` 為月實際支出的平均，`s` 為樣本標準差，`m` 為距離年底的剩餘月數，`B` 為年度預算。

- 若資料從 1 月連續至最新月：點估計 `forecast = YTD actual + m × ȳ`，區間尺度 `scale = s × sqrt(m × (1 + m/n))`。
- 若月份不完整：點估計 `forecast = 12 × ȳ`，區間尺度 `scale = 12 × s / sqrt(n)`。
- 自由度為 `n - 1`，區間使用 Student-t 的 97.5 百分位數：`estimate ± t(0.975, n-1) × scale`；下限截在零。
- 超支機率以 Student-t 右尾計算：`P(年度支出 > B) = t.sf((B - estimate) / scale, df=n-1)`。
- 少於兩個月時無法估計樣本變異，區間和 run-rate 機率回傳 `null`。若資料已完整到 12 月，剩餘月數為零，區間會收斂到觀測全年金額。

這是小樣本 run-rate 近似，沒有處理月度趨勢、自相關、正式季節性、未入帳承諾支出。程式欄位命名為 `ci95`，但尚未回測其 95% 覆蓋率；簡報應稱模型估計區間，並揭露假設與資料限制。

`forecast.overrun_probability` 是 run-rate 機率；`forecast.bayesian_overrun_probability` 是離散貝氏網路機率。兩者模型與意義不同，應分開呈現。

## 6. Z-score 異常偵測

Z-score 用於比較某筆支出與同一項目的歷史分布：

`z = (x - μ) / σ`

其中 `x` 是本月支出，`μ` 是歷史平均，`σ` 是歷史標準差。常見初步規則是 `|z| >= 2` 標示需檢視、`|z| >= 3` 標示高度異常；這些仍需依資料量與企業容忍度確認。

若歷史資料太少或標準差為零，Z-score 不穩定或無法計算。實作時應按部門與費用類別比較同口徑資料，避免把不同費用規模混在一起。目前 API 的 `diagnostics` 為空陣列，Z-score 尚未接入。

## 7. 警示規則草稿

目前燈號依 run-rate 超支機率暫定為：低於 33% 綠燈、33% 至低於 67% 黃燈、67% 以上紅燈。資料不足時應顯示「無法估算」提示，不應把空值當成安全。

執行落後屬於進度警示，與年底預算不足分開計算。草稿以 `progress_gap = YTD actual / annual budget - elapsed_months / 12` 計算，落後至少 10 個百分點提示，至少 20 個百分點標為嚴重。只有完整 YTD 才評估；月份缺漏時應回報資料不足。

門檻尚待 A/B 與教授確認，完整規則見 [`model-assumptions-and-alert-rules.md`](model-assumptions-and-alert-rules.md)。警示目前尚未加入 API JSON。

## 8. API 與互動模擬

- `GET /api/v1/health`：服務健康檢查。
- `POST /api/v1/analyze`：上傳 CSV，回傳 `analysis_id` 與所有部門分析結果。
- `POST /api/v1/simulate`：傳入 `analysis_id` 和部門年度預算覆寫值，重新計算預測與風險。
- FastAPI 的 CORS 中介層允許預設本機前端來源；其他來源用 `BUDGETLENS_CORS_ORIGINS` 設定。
- 貝氏後驗依證據和先驗組合快取，最多 512 組，降低重複 What-If 計算時間。
- 快取分析 CSV 暫存在單一程序記憶體，最多 50 筆；服務重啟或切換 worker 後需要重新上傳。

啟動命令、API JSON 範例和延遲量測見 [`api-forecast-and-simulation.md`](api-forecast-and-simulation.md)。

## 9. 已完成與待辦

**已完成**：CSV 清理與彙總、貝氏網路後驗與模型層反向推論、run-rate 年底預測、95% 估計區間、FastAPI 分析與模擬、後驗快取。

**待完成**：Z-score 診斷 API、部門層級主因與自然語言建議、經確認的預算不足／執行落後 alerts、A 的正式前端串接，以及以多種情境 CSV 進行穩健度與端到端延遲驗證。
