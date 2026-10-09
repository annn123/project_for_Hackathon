# BudgetLens AI

預算 CSV 分析助手。Day 2 目前提供 Python CSV 載入、欄位驗證/正規化，以及部門預算使用率和差異計算；前後端契約見 [`docs/data-contract.md`](docs/data-contract.md)。

## 開發環境

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
py -m pip install -e .
```

## CSV 欄位

必填：`department,period,budget,actual`；`category` 選填。每列代表一個部門/類別/月，`period` 格式為 `YYYY-MM`。詳細 JSON 契約及範例見資料契約文件。

## Python 使用方式

```python
from budgetlens import compute_department_metrics, load_and_clean_csv

data = load_and_clean_csv("budget.csv")
department_cards = compute_department_metrics(data)
```

若要由 repository 根目錄直接執行範例，先安裝專案 `py -m pip install -e .`。
