# WER 排程配置

現行流程見 RUNBOOK.md；本檔只記錄組合與時程。

- WER：每天 08:00，Asia/Taipei，固定前一天為統計日；保留既有 heartbeat chat。
- PR：週二至週六 09:00，Asia/Taipei，保留既有 cron 與 Luna 6 xhigh。
- 各專案決定自己的報告、收件人、主旨、本文及 PDF。WER 沿用原 Nightly 名單加 Elvis；Nightly 正式名單不變。
- 兩排程均透過 Report-Delivery/skill/report-delivery/SKILL.md 組合：準備報告 → 提交 manifest → 共用 Chrome Outlook 寄送 → receipt 同步。
- Luna 無法解決寄送故障時委派 gpt-6.1-sol 分析並嘗試一次有根據恢復；fetch／資料／內容／PDF 驗證失敗則停止。
- 共用端保存附件快照、全域 mailbox 鎖、佇列、防重寄與證據。未知寄送只查寄件備份，不盲目重寄。
- 測試僅寄 Stolas_In@cyberlink.com；不改正式清單。不另建立第三個排程或常駐服務。
- 電腦、Codex、Chrome 必須運行；沿用既有 Outlook 登入。觸發時間不等於保證送達時間。
