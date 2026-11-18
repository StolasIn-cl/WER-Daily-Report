# WER Daily Report｜每日當機案件報告

從 GitHub 看板與自動修復報告整理 WER 當機案件，產生 HTML、PDF 與寄送清單，供每日檢視處理成果與待處理案件。

本專案負責**報告內容、收件者、原主旨、內文與附件**；實際寄送交由 [Report-Delivery](https://github.com/StolasIn-cl/Report-Delivery) 的 Chrome Outlook skill。GitHub 資料僅供讀取，不修改自動修復執行器。

## 專案分工與排程

| 元件 | 負責工作 |
| --- | --- |
| WER-Daily-Report | 擷取資料、整理案件、產生與驗證報告、提供郵件內容與收件者 |
| Report-Delivery | 寄送佇列、附件快照、Chrome Outlook 操作、防重寄與寄件備份查證 |
| Codex 排程 | 串接報告產生、寄送與結果同步 |

**每日 08:00（台灣時間，含週末）**觸發，統計日固定為開始執行時的前一天。Nightly PR 於週二至週六 09:00 觸發，兩者共用寄送鎖。

完整操作流程見 [RUNBOOK.md](RUNBOOK.md)，排程分工見 [schedule-plan.md](schedule-plan.md)。

## 環境與安裝

需要 Python 3.11 以上、已登入且具備來源專案讀取權限的 GitHub CLI（`gh`）。產生 HTML 僅使用 Python 標準函式庫；轉成 PDF 另需 Node.js、Chrome 與 `playwright-core`。

將三個專案放在同一層目錄：

```text
Desktop/
├─ Nightly-PR-Report/
├─ WER-Daily-Report/
└─ Report-Delivery/
```

複製本機設定範本：

```powershell
Copy-Item mail-config.example.json mail-config.local.json
```

| 欄位 | 設定內容 |
| --- | --- |
| `recipients_source` | Nightly 專案的 `recipients.json` 絕對路徑 |
| `extra_recipients` | WER 額外收件者；現行正式設定包含 Elvis |
| `subject_prefix` | 原主旨前綴 `WER Daily Report` |

每次執行都讀取 Nightly 名單、加入額外收件者並去重，不修改 Nightly 原檔。另依 Report-Delivery README 設定其 `config.local.json` 與全域 skill。

`render_report.cjs` 依序尋找 `PLAYWRIGHT_MODULE`、一般 Node.js 套件，以及本機 issue tracker skill 已安裝的 Playwright，不自動安裝套件。

## 每日報告與寄送

下列日期為範例，執行時請換成該次統計日。

```powershell
# 擷取最新資料、產生與驗證報告及寄送清單
python daily_run.py prepare --date 2026-11-09

# 準備完成後，提交共用寄送佇列
python daily_run.py submit --date 2026-11-09
```

接著由排程依共用 skill 完成附檔、寄送與寄件備份查證。只有共用端確認為 `sent` 後，才執行：

```powershell
python daily_run.py sync --date 2026-11-09
```

`prepare` 與 `submit` **都不會自行寄信**。零新增案件仍產生完整報告；已寄過的日期跳過，已有共用工作則恢復原工作，不因重新產生檔案而重寄。舊 `begin-send`、`confirm-sent`、`send-unknown` 指令已移除，不手動寫入成功狀態。

| 輸出位置 | 內容 |
| --- | --- |
| `output/DATE/GENERATION/` | 該次產生的資料、HTML、PDF 與寄送清單 |
| `state/DATE.json` | 本專案的報告準備與同步狀態 |
| `../Report-Delivery/state/` | 共用寄送的權威紀錄與附件快照 |
| `cache/` | 固定提交版本的來源 JSON 快取 |

`GENERATION` 是每次產生報告的獨立目錄。來源快照、報告產物、狀態與本機郵件設定不納入 Git；換機時須另行保留寄送紀錄，避免重寄。測試信**只可寄給 `Stolas_In@cyberlink.com`**。

## 報告內容與統計規則

報告讓 RD 看完就能直接追蹤，不必再回 Boomerang 查最新內容。由上到下：

| 區塊 | 內容 |
| --- | --- |
| 昨日概況 | 昨日新增 crash 數，及其目前狀態分布（未解／處理中／已解完＝待 RD＋已結案）；昨日新進 Done 數字 |
| 目前概況 | 目前 Init 待解數、Handling 數、Handled 等 RD 數、最久已等待天數，並附 Handled 結局分布與時間／成本合計 |
| 昨日新增 crash | 昨日新建立的每一隻 crash 與目前狀態；Init 與 Done 只列標題，不展開細節 |
| 目前處理中（Handling） | 全部處理中案件：進度、接手 RD、執行機器、新增與最後更新時間 |
| 目前等 RD 處理（Handled） | 全部已交付案件，等最久的排最前面；含結局、「為什麼當」（白話根因，附檔案行號）、接手 RD、已等天數、修復 PR 與完整報告連結 |

Init 與 Done 的完整明細不再放入報告；只顯示數字。PDF 為 A4 橫向，表頭跨頁重複，單筆案件不跨頁切開。

### 統計與案件欄位

- **昨日新增**：依 issue 的 `createdAt` 計算，狀態為擷取當下的狀態，不是昨日當天的狀態，也不代表當機發生次數。
- **昨日新進 Done**：以 issue 的 `closedAt` 判斷；沒有關閉時間才退回 Project／issue 最後修改時間（近似值，編輯內容或欄位也會計入）。
- **等待天數**：從 AutoFix 交付時間算到資料擷取時間，3 天以上以紅字標示。
- **為什麼當**：取自 AutoFix 報告 `agent_report.plain.root_cause`（即 issue 留言裡的「為什麼當」欄位）；報告沒有時退回 `ending.root_cause.bad_state_origin` 的機制說明，再沒有就顯示「報告未提供根因」。檔案行號仍來自 `bad_state_origin`。
- **處理時間／模型成本**：目前 Handled 最新一次執行的累計值，不是昨日新增消耗；缺值不視為零。

### 資料來源與解讀限制

| 項目 | 來源與限制 |
| --- | --- |
| 案件範圍 | 完整分頁讀取 GitHub Project #40 與留言，檢查項目數量；讀取失敗就停止，不產生假的零案件報告 |
| 修復結果 | 從 `werfix:publish` 留言取得執行 ID 與固定提交版本的報告 JSON；結局沿用流程的固定中文對照，不代表已合併或 RD 已確認 |
| 處理時間 | 加總 `timelines[].total_seconds`，涵蓋不同處理週期；不是排隊或開始到交付的實際經過時間。缺 timeline 時顯示缺資料 |
| 模型成本 | 使用 `estimated_cost_usd` 並標示估算；是最新一次執行的累計成本，不是訂閱帳單或指定日新增費用 |
| 即時狀態與人員 | 狀態、Assignee、Worker 為擷取當下快照，不是歷史午夜狀態；即時執行進度與當日費用仍需執行器提供摘要 |
| Boomerang 連結 | 優先使用 issue 原始連結並保留日期範圍；有明確 PropID 才產生 RawLog 網址，缺可靠 ID 時標示缺值，不以 issue 或 occurrence 編號代替 |

費用與時間取自結構化報告，不從自然語言推算。Boomerang 目標頁面仍需內網登入權限。

## 手動預覽與離線重建

以下指令僅產生報告，不送信；離線重建只會使用既有資料，不更新來源。

```powershell
# 重新讀取 GitHub，產生指定日的 HTML
python wer_daily_report.py --live --view daily --date 2026-11-09 --out output/preview

# 目前看板快照與 PDF
python wer_daily_report.py --live --view snapshot --pdf

# 合成示意資料與 PDF
python wer_daily_report.py --demo --pdf

# 使用已保存的資料離線重建
python wer_daily_report.py --input output/preview/data.json --view daily --date 2026-11-09 --out output/offline --pdf
```

`data.json` 保存可離線重建的資料。部分既有安裝另有 `output/yesterday/`、`today/`、`live/`、`demo/` 預覽；這些是當時的本機產物，不代表最新資料，也不包含在儲存庫中。

## 驗證與故障處理

```powershell
python -m unittest discover -v
```

資料擷取、日期、報告內容或 PDF 驗證失敗時停止，不寄舊附件。寄送層故障依共用 skill 排查；Luna 6 xhigh 無法解決時，委派 `gpt-6.1-sol` 分析，再嘗試一次有根據的恢復。可能已寄出的結果只能查證，不直接重寄。

遺留的 `preparing` 狀態或 `.lock` 檔須先確認原執行者已停止並查明原因，不直接刪鎖重跑。電腦、Codex、Chrome 與 Outlook 登入仍是排程執行的必要條件。
