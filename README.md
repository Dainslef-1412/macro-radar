# macro-radar 中美宏观雷达

云端定时抓取中美宏观事件，GitHub Pages 展示，**不依赖任何本地电脑**。

## 架构

- **抓取**：`fetch.py`，GitHub Actions 每天 UTC 02:30（北京时间 10:30）运行
- **数据**：东方财富数据中心公开接口（中国 CPI/PPI/LPR、美国 CPI/PPI、美联储利率目标）
- **存储**：事件流 `data/events.json` + 最新快照 `data/latest.json`（随仓库提交）
- **展示**：`index.html`，GitHub Pages 托管，手机浏览器直接访问

## 事件规则（事件触发，非流水账）

- 月度数据（CPI/PPI）：出现新的报告期 → 记事件
- 利率（LPR/联邦基金目标）：读数发生变化 → 记事件
- 无变化则静默，页面只保留干净的事件流

## 手动触发

GitHub 仓库 → Actions → update-macro-events → Run workflow。
