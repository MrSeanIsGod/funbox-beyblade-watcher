# Funbox 戰鬥陀螺 監控機器人 使用說明



## 這支工具做什麼

監控 Funbox 官網「戰鬥陀螺」商品列表頁（<https://shop.funbox.com.tw/collections/戰鬥陀螺>），
用無頭瀏覽器（Playwright）實際渲染頁面後，每隔一段時間檢查：

1. 有沒有全新商品上架
2. 有沒有原本售完的商品重新補貨（可購買）

偵測到就立刻透過 Telegram 傳訊息通知你，訊息裡含商品名稱和連結。

**不會**自動幫你加入購物車或結帳 —— 這是刻意的設計。多數電商官網的服務條款禁止自動化下單，
硬做風險是帳號被鎖或訂單被取消。通知到了之後，麻煩你自己手動點進網站搶購，
速度已經比人工一直刷新頁面快很多。

## 安裝

```bash
pip install playwright requests
playwright install chromium
```

第二行指令會下載無頭瀏覽器（第一次執行需要，檔案有點大，請耐心等待）。

## 設定 Telegram 通知

1. Telegram 搜尋 `@BotFather`，傳送 `/newbot`，依指示取名字，拿到一組 Bot Token
   （長得像 `123456789:AAExxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx`）
2. 用 Telegram 搜尋你剛建立的機器人，傳送任意一句話給它（例如 `hi`）
3. 瀏覽器打開：
   `https://api.telegram.org/bot<你的TOKEN>/getUpdates`
   （把 `<你的TOKEN>` 換成剛剛拿到的 token）
   在回傳的 JSON 裡找 `"chat":{"id": 數字}`，這個數字就是你的 Chat ID
4. 先執行一次 `python funbox_beyblade_watcher.py`（或打包後的 exe），
   它會在同資料夾自動產生一份 `config.json` 範例檔然後結束執行
5. 打開 `config.json`，把 `BOT_TOKEN` 和 `CHAT_ID` 換成你拿到的值：
   ```json
   {
     "BOT_TOKEN": "123456789:AAExxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
     "CHAT_ID": "987654321",
     "CHECK_INTERVAL": 20
   }
   ```
   之後想調整檢查頻率或換 Token，直接改這個檔案就好，不用改程式碼、
   打包成 exe 的話也不用重新打包

## 執行

```bash
python funbox_beyblade_watcher.py
```

第一次執行時，程式會先記錄目前所有商品當作基準（不會發通知，避免把現有幾十款商品當新品狂發），
之後每次檢查到「新增」的商品才會通知你。

程式需要一直保持執行狀態才有作用。想長時間掛著監控，可以考慮：
- 留一台電腦或筆電開機跑著（最簡單）
- 用便宜的小型 VPS（例如國內外的雲端主機）24 小時跑
- 用 `screen` / `tmux` 讓程式在背景持續執行，不怕關掉終端機視窗

## 調整監控頻率

改 `config.json` 裡的 `CHECK_INTERVAL`（單位：秒）。因為這版要開瀏覽器實際渲染頁面
（比純抓 HTML 慢一點、也吃資源一些），不建議調到低於 15 秒，避免對網站造成負擔或
被判定為異常流量。

## 打包成 Windows exe（選用）

如果不想每次都開命令列跑 Python，可以打包成 .exe，之後點兩下就能執行：

```bash
pip install pyinstaller
pyinstaller --onefile --name FunboxWatcher funbox_beyblade_watcher.py
```

打包完成後 `dist` 資料夾裡會有 `FunboxWatcher.exe`。注意兩點：

- 執行 exe 的電腦上仍然需要先跑過 `playwright install chromium` 一次
  （瀏覽器核心不會被打包進 exe 裡，是額外裝在系統目錄下）
- 把 `FunboxWatcher.exe` 放到你想執行的資料夾，跑一次讓它生出 `config.json`，
  填好 Token 之後就能直接雙擊執行，不用裝 Python

## 限制與注意事項

- 靠解析網頁 DOM 結構（找 `<a href="/products/...">` 附近的文字）判斷商品名稱、
  價格、庫存狀態，如果 Funbox 網站改版（例如換了框架或大幅調整版面），
  這個抓取邏輯可能需要跟著調整
- 商品較多時會自動翻頁抓完整清單，目前上限 8 頁，如果分類商品超過這個範圍需要調整
  `MAX_PAGES`
- 通知到了之後，最終還是要你自己手動下單完成搶購
- 開瀏覽器渲染比純文字抓取吃資源，長時間掛著建議放在效能還可以的電腦或 VPS 上
