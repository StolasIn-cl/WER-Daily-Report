# 正式寄送交接（2026-11-10 起）

## 從 GitHub 安裝

將本專案與 Nightly-PR-Report、Report-Delivery 放在同一層目錄。複製 `mail-config.example.json` 為 `mail-config.local.json`，設定既有收件名單路徑與額外收件者；不要將正式名單提交到 Git。Report-Delivery 的 `config.local.json` 也需依其 README 設定。需要 Python 3.11+、已登入且可讀來源專案的 GitHub CLI，以及 PDF renderer 使用的 Node/Playwright/Chrome。全域寄送 skill 與排程依 RUNBOOK.md 設定。

`python -m unittest discover -v` 可執行離線測試。`cache/`、`output/`、`state/`、來源快照與本機郵件設定均不納入版本控制；既有安裝的寄送紀錄須另外保留，不能因重新 clone 而重寄。

本專案只產生報告、收件者、原主旨本文與 delivery manifest；共用 Report-Delivery 模組／report-delivery skill 負責 Chrome Outlook 寄送。流程與現行指令以 RUNBOOK.md 為準：daily_run.py prepare、submit、共用寄送、shared sent 後 sync。舊 begin-send／confirm-sent 指令已移除。每日 08:00 台灣時間；PR 排程改到 09:00。測試只寄 Stolas_In@cyberlink.com。generation 輸出保存在 output/DATE/GENERATION/。

---

# WER daily report — local script prototype

固定目錄：`C:/Users/stolas_in/Desktop/WER-Daily-Report`。HTML 與 PDF 由固定腳本產生；自動寄送流程見 `RUNBOOK.md`，每天 08:00（Asia/Taipei）準備前一天報告並透過 Outlook Web 寄出。GitHub 僅讀取，不修改 runner。

`daily_run.py prepare` 只準備附件、收件人與寄送狀態，不會自行寄信；寄送由排程 agent 依 `RUNBOOK.md` 使用已登入的 Chrome／Outlook 操作。每次讀取 Nightly 的收件人清單，加 Elvis 並去重；不修改 Nightly 原清單。寄出確認後才記錄成功。

## 已產出的預覽

- `output/yesterday/report.html`、`report.pdf`：2026-11-09 新增統計、全部 Handled／Handling／Init，以及當日最後修改的 Done。
- `output/today/report.html`、`report.pdf`：2026-11-10 修改日報，供確認今天手動移動的案件。
- `output/live/report.html`、`report.pdf`：目前全部 Handled／Handling／Init 的真實看板快照。
- `output/demo/report.html`、`report.pdf`：合成示意，包含目前真實看板沒有的 Handling 列。

四區依序為 Handled、Done、Handling、Init；Handled 上方有統計。每列包含標題與 issue link、Boomerang、結局／進度、接手 RD、執行機器、處理時間、模型成本。沒有人工評價或 O／!／X。已放大標題、欄位、連結及備註文字，PDF 同步放大。

每日版：Handled／Handling／Init 顯示全部目前案件，供 RD 檢視，不限日期。Done 只列指定日（預設前一天，台灣時間）最後修改的案件。修改時間取 issue 與 Project 項目 `updatedAt` 較新者，涵蓋內容、欄位及手動移動狀態；不是精確狀態轉換時間。每列用獨立日期色塊顯示完整新增／修改日期及時間。快照版仍保留全部 Done。

最上方「新增案件統計」依 issue `createdAt` 計算指定日新建的 crash issue，分成目前 Init／Handling／Handled；新增後目前 Done 的數量另註明並納入總數。這是新建 issue 數，不是狀態轉換次數或實際 crash occurrence 數；統計範圍為目前 Project #40 中的 issue。下方另保留「待檢視成果統計」，呈現目前全部 Handled 的最新 run 累計時間／成本，與新增統計分開。缺建立時間的舊離線資料需先用 `--live` 更新。

呈現更新：新增統計為總數／Init／Handling／Handled／Done 五格，使用淡綠底；目前 Handled 成果統計改用白底。處理時間與模型成本合計下方列平均值，分別只計算有時間或成本的案件，並顯示有效筆數／總數；零值算有效資料，缺值不當成零。中位數已移除，改顯示「目前 Handled 最早交付」日期與 issue 連結。Handled 依最新 run 的交付時間由早到晚排序，缺交付時間者置後；不將最後修改時間視為等待起點。

閱讀版配色改為暖白／深灰文字，新增統計使用淡藍灰底，連結及重點使用低彩度藍綠。保留第一頁的統計密度；案件頁放大 PDF 標題與內文，增加行距、列高與日期區留白，允許較多頁數。表頭跨頁重複，每筆案件不跨頁切開；執行機器欄加寬，避免名稱尾碼單獨換行。

每筆案例加獨立圓角框線、淡底色及卡片間距。每個 group 從 1 獨立編號，同 group 跨頁接續計數。PDF 各 group 強制從新頁開始；group 大標題、件數、說明與欄位表頭置於可跨頁重複的表頭中，續頁仍能辨識目前區塊。HTML 保留連續捲動版面。

Boomerang 優先沿用 issue 內原始連結，保留日期範圍。若只有明確 PropID／intake 標記／數字標題 ID，則產生已知 RawLog 格式的網址；沒有可靠 ID 時顯示「Boomerang（缺 PropID）」，不將 occurrence 編號或 issue 編號當成 PropID。連結擷取與產生不需要 LLM；目標頁面仍使用內網登入權限。

## 執行

在本目錄執行，Python 只用標準函式庫：

```powershell
# 重新讀取 GitHub：只需已登入的 gh，HTML 不依賴 Node
python wer_daily_report.py --live --view daily --date 2026-11-09 --out output/yesterday

# 目前看板快照與 PDF
python wer_daily_report.py --live --view snapshot --pdf

# 示意版
python wer_daily_report.py --demo --pdf

# 離線重建：沿用已保存的資料，不重新呼叫 GitHub
python wer_daily_report.py --input output/yesterday/data.json --view daily --date 2026-11-10 --out output/today --pdf

# 驗證統計、時區、去重、缺值與輸出
python -m unittest discover -s . -p test_wer_daily_report.py
```

轉 PDF 額外需要 Node、Chrome 與既有 Playwright。`render_report.cjs` 優先讀取 `PLAYWRIGHT_MODULE`，其次嘗試一般 Node 套件，最後使用本機 issue tracker skill 已安裝的 Playwright。沒有自動安裝套件。PDF 為 A4 橫向，表頭跨頁重複。

## 資料來源與目前的界線

- 全量讀取 GitHub Project #40；檢查 item 數量，留言完整分頁。來源讀取失敗就退出，不生成假零資料。
- 從 `werfix:publish` 留言找出 run ID 與固定 commit 的報告 JSON，再透過 GitHub API 取得原始資料。費用／時間不解析自然語言。
- 結局為 pipeline 原始 ending 的固定中文對照。與「是否已合併／RD 是否確認」分開。
- 處理時間為 report `timelines[].total_seconds` 加總，涵蓋不同 cycle；不是排隊或開始到交付的牆鐘時間。沒有 timeline 時顯示缺資料，不以僅含最後 cycle 的 `stages` 偽裝完整時間。
- 模型成本為報告 `estimated_cost_usd`，保留是否含估算；不是訂閱帳單。缺值顯示尚無資料。
- 各版每案只顯示最新 run 的結局與累計時間／成本；Handled 顯示全部，不依交付日或修改日篩選。
- 金額是所選案件最新 run 的累計，**不是該日 session 的新增消耗**。要算後者，仍需 runner 提供 session 時間與費用摘要。
- 修改時間使用 GraphQL 完整分頁讀取 issue／Project updatedAt；不以 issue createdAt 或 closedAt 篩選。最近又被修改的案件會移至較新的修改日。
- Done／Handling／Init 是資料擷取當下的快照，並非歷史午夜快照。Handling 的即時階段、累計消耗尚待 runner 摘要，不能拿舊 run 的報告充數。
- Assignee／Worker 為目前 Project 讀回值；昨日交付案件的歷史接手人尚無快照。

`data.json` 保存可離線重建的投影；`cache/` 只快取固定 commit 的來源 JSON。截圖與 `browser-check.json` 是本次版面驗證證據。這些都留在本機試作目錄。

排程與共用寄送整合以本文件開頭及 RUNBOOK.md 為準；交付／session 摘要的資料限制如上。
