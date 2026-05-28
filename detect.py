import cloudscraper
import json
import requests
import os
import urllib.parse
import re
import redis

# ================= 你的設定區 =================
LINE_CHANNEL_ACCESS_TOKEN = os.environ.get('LINE_CHANNEL_ACCESS_TOKEN', '')
LINE_USER_ID = os.environ.get('LINE_USER_ID', '')

# 設定你的搜尋目標與預算條件
SEARCH_KEYWORD = os.environ.get('SEARCH_KEYWORD', 'チェンソーマン')
MIN_PRICE = int(os.environ.get('MIN_PRICE', '2000'))

REDIS_URL = os.environ.get('REDIS_URL', '')
# ==============================================

def get_redis_client():
    return redis.from_url(REDIS_URL, decode_responses=True)

def is_seen(item_id):
    r = get_redis_client()
    return r.sismember('seen_items', item_id)

def mark_seen(item_id):
    r = get_redis_client()
    r.sadd('seen_items', item_id)

def send_line_message(text):
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
    scraper = cloudscraper.create_scraper(browser={
        'browser': 'chrome',
        'platform': 'windows',
        'desktop': True
    })

    encoded_keyword = urllib.parse.quote(SEARCH_KEYWORD)
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
            clean_text = response.text.replace('\\"', '"')

            # 嘗試多種 pattern，增加容錯率
            patterns = [
                r'"initialItems":(\[.*?\]),"initialPageToken"',
                r'"initialItems":(\[.*?\]),"total"',
                r'"initialItems":(\[.*?\])',
            ]

            items_data = []
            for pattern in patterns:
                match = re.search(pattern, clean_text, re.DOTALL)
                if match:
                    items_json_str = match.group(1)
                    try:
                        items_data = json.loads(items_json_str)
                        print(f"✅ 成功解析，共找到 {len(items_data)} 筆商品。")
                        return items_data
                    except json.JSONDecodeError as e:
                        print(f"⚠️ JSON 轉換失敗 (pattern={pattern}): {e}")
                        continue

            print("⚠️ 解析失敗：所有 pattern 均無法 match。")
            # 輸出部分 HTML 供 debug
            snippet = clean_text[:2000] if len(clean_text) > 2000 else clean_text
            print(f"--- HTML 前 2000 字 ---\n{snippet}\n---")
            return []
        else:
            print(f"抓取失敗，狀態碼: {response.status_code}")
            return []
    except Exception as e:
        print(f"發生錯誤: {e}")
        return []

def main():
    print(f"開始搜尋: {SEARCH_KEYWORD} ...")
    items = fetch_mercari_tw_items()

    new_items_found = 0
    for item in items:
        try:
            item_id = str(item.get('id', ''))
            if not item_id:
                continue
            title = item.get('title', '未命名商品')

            price_info = item.get('price', {})
            raw_price = price_info.get('formattedAmount', '0')
            # 去除所有非數字字元（如貨幣符號、逗號）
            price_digits = re.sub(r'[^\d]', '', raw_price)
            price = int(price_digits) if price_digits else 0

            item_url = f"https://tw.mercari.com/zh-hant/items/{item_id}"

            print(f"雷達掃描到：{title} / 價格：{price} / ID：{item_id}")

            if price >= MIN_PRICE and not is_seen(item_id):
                msg = f"🔥【新上架提醒】🔥\n名稱: {title}\n價格: {price} 台幣\n連結: {item_url}"
                send_line_message(msg)
                print(msg)
                mark_seen(item_id)
                new_items_found += 1
        except Exception as e:
            print(f"⚠️ 處理商品時發生錯誤 ({item}): {e}")
            continue

    print(f"本次掃描完成，共找到 {new_items_found} 筆符合條件的新商品。")

if __name__ == "__main__":
    main()
