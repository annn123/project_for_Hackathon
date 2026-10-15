# BudgetLens AI

預算 CSV 分析助手。包含 Python CSV 載入、欄位驗證/正規化、部門預算使用率與差異計算、貝氏網路超支風險，以及 FastAPI 分析與 What-If API。另附可直接使用的互動式前端儀表板。前後端契約見 [`docs/data-contract.md`](docs/data-contract.md)，模型假設見 [`docs/bayesian-model.md`](docs/bayesian-model.md)。

## 開發環境

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e .
```

## CSV 欄位

必填：`department,period,budget,actual`；`category` 選填。每列代表一個部門/類別/月，`period` 格式為 `YYYY-MM`。詳細 JSON 契約及範例見資料契約文件。

## Python 使用方式

```python
from budgetlens import compute_department_metrics, load_and_clean_csv

data = load_and_clean_csv("budget.csv")
department_cards = compute_department_metrics(data)
```

模型後驗機率範例：

```python
from budgetlens import infer_overrun_causes, posterior_overrun_probability

probability = posterior_overrun_probability({
    "歷史速率": "快",
    "當前進度": "提前",
    "季節因素": "旺季",
})
possible_causes = infer_overrun_causes()
```

若要由 repository 根目錄直接執行範例，先安裝專案 `python -m pip install -e .`。

## API MVP

```powershell
python -m pip install -e .
python -m uvicorn budgetlens.api:app --reload
```

啟動後開啟 `http://127.0.0.1:8000/` 使用儀表板，或前往 `http://127.0.0.1:8000/docs` 操作 API；端點與假設見 [`docs/api-forecast-and-simulation.md`](docs/api-forecast-and-simulation.md)。

### 在 VS Code 測試

1. 用 VS Code 開啟專案資料夾，安裝建議的 **Python** 擴充套件，並選擇 Python 3.10 以上的直譯器（`Ctrl+Shift+P` → **Python: Select Interpreter**）。
2. 開啟 VS Code 終端機（`Ctrl+` `` ` ``），執行 `python -m pip install -e .` 安裝依賴。
3. 按 `Ctrl+Shift+P` → **Tasks: Run Task** → **BudgetLens: 啟動 API 與儀表板**。
4. 瀏覽器開啟 <http://127.0.0.1:8000/>，上傳 `sample_budget.csv` 試用。停止服務時，在終端機按 `Ctrl+C`。
