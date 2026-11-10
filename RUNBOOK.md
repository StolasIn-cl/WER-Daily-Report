# WER 每日報告與寄送交接

每天 08:00（Asia/Taipei）觸發，統計日為開始工作時固定的台灣時間前一天 DATE。包含週末。根目錄：C:/Users/stolas_in/Desktop/WER-Daily-Report。

本專案負責報告與郵件內容；Outlook 寄送由獨立 C:/Users/stolas_in/Desktop/Report-Delivery 與 C:/Users/stolas_in/.codex/skills/report-delivery/SKILL.md 維護，不再引用 Nightly 的寄送程序。

## 產生報告

`python C:/Users/stolas_in/Desktop/WER-Daily-Report/daily_run.py prepare --date DATE`

讀最新 GitHub 資料，Handled／Handling／Init 顯示全部，Done 依統計日最後修改篩選。各 group 從 1 編號、跨頁續編。產生 generation 獨立目錄中的 HTML、PDF 與 delivery-manifest.json；state/DATE.json 是報告準備狀態。每次讀 Nightly recipients.json 加 Elvis 並去重，本專案決定原有主旨、本文與有日期的 PDF。零新增案件仍寄完整報告。

- ready：讀回報的完整 manifest 路徑，供下一階段提交。
- skip_already_sent：停止，沿用舊確認寄送紀錄。
- existing_delivery：共用端已有該日期工作，按其 sent／queued／unknown 等狀態跳過或恢復，不重新生成。
- API、日期、內容或 PDF 驗證失敗：停止並回報，不寄舊附件，不委派 Sol 強迫寄出。
- 本地 preparing 或 .lock 遺留：先查原執行者是否停止與中斷原因；不能直接刪鎖重跑。

## 排程組合寄送

1. 先讀共用 skill 並查看 delivery.py list，依其規則恢復中斷工作。只查證不明寄送，不能直接重寄。
2. 執行 prepare。
3. ready 後執行 `python C:/Users/stolas_in/Desktop/WER-Daily-Report/daily_run.py submit --date DATE`。提交 manifest 到共用端，建立附件快照／佇列，不自行寄信。
4. 依共用 skill claim、操作 Chrome extension 的 Outlook、附檔、確認完整收件者與原主旨本文，再寄出。每日寄給固定清單已有使用者授權，不加模型自發確認。工具或登入實際阻擋仍須遵守。
5. Luna 6 xhigh 無法解決的寄送問題，依共用 skill 委派 gpt-6.1-sol 分析，再執行一次有根據的恢復；不能改收件者／報告、繞過拒絕或重按 Send。不是一遇到錯誤就結束。
6. 共用端確認 sent 後執行 `python C:/Users/stolas_in/Desktop/WER-Daily-Report/daily_run.py sync --date DATE`，從 receipt 更新原 state，不能自行寫 email_sent=true。
7. 共用 skill 繼續處理先前留下的 queued 報告，保留各自原日期。最後回報報告日期、件數、收件者人數、sent／unknown／blocked／queued 及輸出路徑。

寄送狀態唯一權威是 Report-Delivery/state/delivery.sqlite3。舊 begin-send／confirm-sent／send-unknown CLI 已移除，改用共用狀態機。測試只可寄 Stolas_In@cyberlink.com；正式清單不變。電腦、Codex、Chrome 與 Outlook 登入仍是執行依賴。實際寄出後不明的結果只查寄件備份，不盲目重寄。
