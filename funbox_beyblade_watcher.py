#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Funbox 戰鬥陀螺 監控 + Telegram 通知 機器人 ( Playwright 版)
=====================================================================

上一版用 requests 直接抓 HTML，抓到的商品 ID 清單其實不是真正顯示在網頁上的商品
（Funbox 的商品清單是用 JavaScript 動態渲染的），所以會誤報。

這一版改用 Playwright 開一個無頭瀏覽器實際「跑」JS，抓到的內容跟你自己用瀏覽器看到的
一模一樣，準確度高很多。

功能：
  1. 每隔 CHECK_INTERVAL 秒重新載入商品列表頁（會自動翻頁抓完整清單）
  2. 偵測「全新商品上架」→ 立刻通知
  3. 偵測「原本售完 → 補貨可購買」→ 立刻通知（很多熱門陀螺是用這種方式補貨的）

這支程式「不會」自動幫你加入購物車或結帳，原因跟上一版一樣：多數電商官網禁止自動化
下單，硬做風險是帳號被鎖或訂單被取消。收到通知後，麻煩自己手動搶購。

使用前準備：
  1. pip install playwright requests
  2. playwright install chromium        <- 這一步會下載無頭瀏覽器，第一次要跑
  3. 填好下面的 BOT_TOKEN 和 CHAT_ID
"""

import json
import re
import time
import traceback
from datetime import datetime
from pathlib import Path

import requests
from playwright.sync_api import sync_playwright

# ============================================================
# 設定區
# ============================================================
# 這幾個值優先從同一個資料夾的 config.json 讀取（打包成 exe 後，改設定
# 不用重新打包，直接編輯 config.json 即可）。找不到 config.json 時，
# 才會用下面這幾個預設值。
#
# config.json 範例：
# {
#   "BOT_TOKEN": "123456789:AAExxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
#   "CHAT_ID": "987654321",
#   "CHECK_INTERVAL": 20
# }

DEFAULT_BOT_TOKEN = "請填入你的_BOT_TOKEN"
DEFAULT_CHAT_ID = "請填入你的_CHAT_ID"
DEFAULT_CHECK_INTERVAL = 20

# 商品列表頁（戰鬥陀螺整個分類）
COLLECTION_URL = "https://shop.funbox.com.tw/collections/戰鬥陀螺"

# 最多翻幾頁（安全上限，避免商品很多時無限翻頁）
MAX_PAGES = 8

# 支援打包成 exe：sys.executable 所在資料夾優先，否則用程式檔案所在資料夾
import sys

if getattr(sys, "frozen", False):
    BASE_DIR = Path(sys.executable).parent
else:
    BASE_DIR = Path(__file__).parent

CONFIG_FILE = BASE_DIR / "config.json"
STATE_FILE = BASE_DIR / "funbox_seen_products.json"


def load_config():
    cfg = {
        "BOT_TOKEN": DEFAULT_BOT_TOKEN,
        "CHAT_ID": DEFAULT_CHAT_ID,
        "CHECK_INTERVAL": DEFAULT_CHECK_INTERVAL,
    }
    if CONFIG_FILE.exists():
        try:
            user_cfg = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
            cfg.update(user_cfg)
        except Exception as e:
            print(f"⚠️ config.json 讀取失敗，使用預設值：{e}")
    else:
        # 第一次執行，順手產生一份範例 config.json 給使用者填
        try:
            CONFIG_FILE.write_text(
                json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            print(f"📝 已在 {CONFIG_FILE} 產生設定檔範例，請填入你的 BOT_TOKEN 和 CHAT_ID 後重新執行。")
        except Exception:
            pass
    return cfg


CONFIG = load_config()
BOT_TOKEN = CONFIG["BOT_TOKEN"]
CHAT_ID = CONFIG["CHAT_ID"]
CHECK_INTERVAL = CONFIG["CHECK_INTERVAL"]

# ============================================================
# 以下不用改
# ============================================================

SOLDOUT_KEYWORDS = ["商品已售完", "已售完", "售完"]


def log(msg: str):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


def send_telegram(text: str):
    if "請填入" in BOT_TOKEN or "請填入" in CHAT_ID:
        log("⚠️ 尚未設定 BOT_TOKEN / CHAT_ID，無法送出 Telegram 通知，請先完成設定。")
        return
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    try:
        resp = requests.post(
            url,
            data={"chat_id": CHAT_ID, "text": text, "disable_web_page_preview": False},
            timeout=10,
        )
        if resp.status_code != 200:
            log(f"❌ Telegram 通知傳送失敗：{resp.status_code} {resp.text}")
    except Exception as e:
        log(f"❌ Telegram 通知傳送發生例外：{e}")


def extract_products(page) -> dict:
    """
    從目前頁面的 DOM 裡抓出所有商品卡片。
    用「所有指向 /products/ 的連結」當錨點，往上找最近的區塊容器，
    把容器內的文字（含商品名、價格、庫存狀態）一起抓出來。
    回傳 {商品網址: {"name":..., "text":..., "soldout": bool}}
    """
    raw = page.evaluate(
        """
        () => {
            const anchors = Array.from(document.querySelectorAll('a[href*="/products/"]'));
            const seen = new Set();
            const items = [];
            for (const a of anchors) {
                const href = a.href.split('?')[0];
                if (seen.has(href)) continue;
                seen.add(href);
                let container = a.closest('li') || a.closest('div');
                let text = container ? container.innerText : a.innerText;
                items.push({url: href, text: (text || '').trim()});
            }
            return items;
        }
        """
    )
    result = {}
    for item in raw:
        url = item["url"]
        text = item["text"]
        soldout = any(kw in text for kw in SOLDOUT_KEYWORDS)
        # 商品名稱通常是文字區塊的第一行
        name = text.split("\n")[0].strip() if text else url
        result[url] = {"name": name, "text": text, "soldout": soldout}
    return result


def fetch_all_products(playwright) -> dict:
    """翻頁抓完整的商品清單"""
    browser = playwright.chromium.launch(headless=True)
    page = browser.new_page(
        user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        )
    )
    all_products = {}
    try:
        for page_num in range(1, MAX_PAGES + 1):
            url = COLLECTION_URL if page_num == 1 else f"{COLLECTION_URL}?page={page_num}"
            page.goto(url, wait_until="networkidle", timeout=30000)
            # 保險等一下，確保 JS 渲染完成
            page.wait_for_timeout(1500)

            products = extract_products(page)
            if not products:
                break

            new_on_this_page = {k: v for k, v in products.items() if k not in all_products}
            all_products.update(products)

            if not new_on_this_page:
                # 這頁抓到的東西跟前面完全重複，代表沒有下一頁了
                break
    finally:
        browser.close()
    return all_products


def load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def save_state(state: dict):
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def main():
    log("啟動監控（Playwright 版）：Funbox 戰鬥陀螺分類頁")
    log(f"目標網址：{COLLECTION_URL}")
    log(f"檢查間隔：{CHECK_INTERVAL} 秒")

    state = load_state()

    with sync_playwright() as p:

        if not state:
            try:
                products = fetch_all_products(p)
            except Exception as e:
                log(f"初始化失敗：{e}")
                traceback.print_exc()
                return
            state = {
                url: {"name": info["name"], "soldout": info["soldout"]}
                for url, info in products.items()
            }
            save_state(state)
            log(f"首次執行，已記錄目前 {len(state)} 件商品作為基準。")
            send_telegram(
                f"✅ 監控已啟動\n目前戰鬥陀螺分類共有 {len(state)} 件商品作為基準。\n"
                f"之後有新商品上架，或原本售完的商品補貨，會立刻通知你。"
            )

        consecutive_errors = 0

        while True:
            time.sleep(CHECK_INTERVAL)
            try:
                products = fetch_all_products(p)
                consecutive_errors = 0
            except Exception as e:
                consecutive_errors += 1
                log(f"抓取失敗（連續第 {consecutive_errors} 次）：{e}")
                if consecutive_errors >= 5:
                    log("連續失敗次數過多，暫停 120 秒後再繼續...")
                    time.sleep(120)
                continue

            new_items = []
            restocked_items = []

            for url, info in products.items():
                prev = state.get(url)
                if prev is None:
                    new_items.append((url, info["name"]))
                elif prev.get("soldout") and not info["soldout"]:
                    restocked_items.append((url, info["name"]))

            if new_items:
                lines = "\n".join(f"• {name}\n  {url}" for url, name in new_items)
                log(f"🎉 偵測到新商品上架：{[n for _, n in new_items]}")
                send_telegram(f"🚨 戰鬥陀螺有新商品上架！\n\n{lines}")

            if restocked_items:
                lines = "\n".join(f"• {name}\n  {url}" for url, name in restocked_items)
                log(f"📦 偵測到補貨：{[n for _, n in restocked_items]}")
                send_telegram(f"📦 售完商品補貨了！\n\n{lines}")

            if not new_items and not restocked_items:
                log(f"目前無變化（現有 {len(products)} 件商品）")

            state = {
                url: {"name": info["name"], "soldout": info["soldout"]}
                for url, info in products.items()
            }
            save_state(state)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log("使用者中止程式。")
