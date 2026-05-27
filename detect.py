import cloudscraper
import json
import time
import requests
import os
import urllib.parse
import re

# ================= 你的設定區 =================
LINE_CHANNEL_ACCESS_TOKEN = 'X9eT9P1zMl/arJDkI+76WOHWw5D7ybyGRAR6Ms870WjlL9ujlg1HLU2DIR5C6/p5CduNMu4V8kblvQcKGL6Rk9OOfKBJzUuj45qNDqaD5y7Z95SAQMjjMSmF3GywD20gdTyKx9oc639+iw0se8oC9QdB04t89/1O/w1cDnyilFU='
LINE_USER_ID = 'Ue59e1bddb3a14cb0acf72f21354240d0'

# 設定你的搜尋目標與預算條件
SEARCH_KEYWORD = 'チェンソーマン'
MIN_PRICE = 2000 

SEEN_ITEMS_FILE = 'seen_items.txt'
# ==============================================

def load_seen_items():
    if not os.path.exists(SEEN_ITEMS_FILE):
        return set()
    with open(SEEN_ITEMS_FILE, 'r') as f:
        return set(line.strip() for line in f)

def save_seen_item(item_id):
    with open(SEEN_ITEMS_FILE, 'a') as f:
        f.write(f"{item_id}\n")

def send_line_message(text):
    """透過 LINE Messaging API 發送推播到你的手機"""
    url = 'https://api.line.me/v2/bot/message/push'
    headers = {
        'Content-Type': 'application/json',
        'Authorization': f'Bearer {LINE_CHANNEL_ACCESS_TOKEN}'
    }
    data = {
        "to": LINE_USER_ID,
        "messages": [{"type": "text", "text": text}]
    }
    response = requests.post(url, headers=headers, json=data)
    if response.status_code != 200:
        print(f"LINE 傳送失敗: {response.text}")

def fetch_mercari_tw_items():
    """使用 cloudscraper 抓取台灣版 Mercari 資料 (暴力解析版)"""
    scraper = cloudscraper.create_scraper(browser={
        'browser': 'chrome',
        'platform': 'windows',
        'desktop': True
    })
    
    encoded_keyword = urllib.parse.quote(SEARCH_KEYWORD)
    
    # 💡 關鍵改變：直接請求最乾淨的搜尋網址，把麻煩的 _rsc 跟 category-ids 都拿掉
    api_url = f"https://tw.mercari.com/zh-hant/search?keyword={encoded_keyword}&sort=1" 
    
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
            'Accept-Language': 'zh-TW,zh;q=0.9,en-US;q=0.8,en;q=0.7'
        }
        
        response = scraper.get(api_url, headers=headers)
        response.encoding = 'utf-8' 
        
        if response.status_code == 200:
            # 將跳脫字元還原
            clean_text = response.text.replace('\\"', '"')
            
            # 💡 強化版：直接鎖定整包 initialItems 陣列，不再逐行比對
            match = re.search(r'"initialItems":(\[\{"\$typeName":"bff\.applications\.search\.v1\.Item".*?\}\]),"initialPageToken"', clean_text)
            
            if match:
                items_json_str = match.group(1)
                # 為了避免 JSON 結尾逗號等格式錯誤，做個簡單的修復
                try:
                    items_data = json.loads(items_json_str)
                    return items_data
                except json.JSONDecodeError:
                    print("⚠️ JSON 轉換失敗，正在嘗試修復字串...")
                    return []
            else:
                print("⚠️ 解析失敗：找不到商品陣列。")
                return []
                
        else:
            print(f"抓取失敗，狀態碼: {response.status_code}")
            return []
    except Exception as e:
        print(f"發生錯誤: {e}")
        return []

def main():
    print(f"開始搜尋: {SEARCH_KEYWORD} ...")
    seen_items = load_seen_items()
    items = fetch_mercari_tw_items()
    
    new_items_found = 0
    for item in items:
        # 拿回原本的 UUID
        item_id = str(item.get('id', ''))
        title = item.get('title', '未命名商品')
        
        price_info = item.get('price', {})
        raw_price = price_info.get('formattedAmount', '0')
        price = int(raw_price.replace(',', ''))
        
        # 💡 終極修正：把 item 改成 items (加 s)
        item_url = f"https://tw.mercari.com/zh-hant/items/{item_id}"
        
        print(f"雷達掃描到 📡：{title} / 價格：{price}")
        
        if price >= MIN_PRICE and item_id not in seen_items:
            msg = f"🔥【新上架提醒】🔥\n名稱: {title}\n價格: {price} 台幣\n連結: {item_url}"
            send_line_message(msg) 
            print(msg)
            save_seen_item(item_id)
            seen_items.add(item_id)
            new_items_found += 1
            
    print(f"本次掃描完成，共找到 {new_items_found} 筆符合條件的新商品。")

if __name__ == "__main__":
    # 讓程式一啟動就進入無限循環
    while True:
        try:
            main()
        except Exception as e:
            print(f"循環執行時發生未知錯誤: {e}")
        
        # 300 秒 = 5 分鐘
        print("⏳ 等待 5 分鐘後將進行下一次雷達掃描...\n")
        time.sleep(300)