import cloudscraper
import json
import requests
import os
import urllib.parse
import re
import redis
import time
import random

# ================= 你的設定區 =================
LINE_CHANNEL_ACCESS_TOKEN = os.environ.get('LINE_CHANNEL_ACCESS_TOKEN', '')
LINE_USER_ID = os.environ.get('LINE_USER_ID', '')

SEARCH_KEYWORD = os.environ.get('SEARCH_KEYWORD', 'チェンソーマン')
MIN_PRICE = int(os.environ.get('MIN_PRICE', '2000'))

REDIS_URL = os.environ.get('REDIS_URL', '')

MAX_RETRIES = 3
RETRY_DELAY = 5  # 秒
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

def extract_json_array(html, start):
    """括弧深度追蹤，正確取出完整 JSON 陣列，不受 ] 在字串中影響。"""
    if start >= len(html) or html[start] != '[':
        return None
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(html)):
        c = html[i]
        if esc:
            esc = False
            continue
        if c == '\\' and in_str:
            esc = True
            continue
        if c == '"':
            in_str = not in_str
            continue
        if in_str:
            continue
        if c in '[{':
            depth += 1
        elif c in ']}':
            depth -= 1
            if depth == 0:
                return html[start:i + 1]
    return None

def is_cloudflare_block(html, status_code):
    """偵測是否被 Cloudflare 攔截。"""
    if status_code in (403, 429, 503):
        return True
    cf_signals = [
        'cf-browser-verification',
        'cf_clearance',
        'Checking if the site connection is secure',
        'Enable JavaScript and cookies to continue',
        'cloudflare',
        'Just a moment',
    ]
    html_lower = html[:2000].lower()
    return any(sig.lower() in html_lower for sig in cf_signals)

def fetch_mercari_tw_items():
    encoded_keyword = urllib.parse.quote(SEARCH_KEYWORD)
    api_url = f"https://tw.mercari.com/zh-hant/search?keyword={encoded_keyword}&sort=1"

    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8',
        'Accept-Language': 'zh-TW,zh;q=0.9,en-US;q=0.8,en;q=0.7',
        'Accept-Encoding': 'gzip, deflate, br',
        'Referer': 'https://tw.mercari.com/',
        'sec-ch-ua': '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
        'sec-ch-ua-mobile': '?0',
        'sec-ch-ua-platform': '"Windows"',
        'sec-fetch-dest': 'document',
        'sec-fetch-mode': 'navigate',
        'sec-fetch-site': 'same-origin',
        'sec-fetch-user': '?1',
        'upgrade-insecure-requests': '1',
        'cache-control': 'max-age=0',
    }

    for attempt in range(1, MAX_RETRIES + 1):
        # 隨機延遲，降低被鎖 IP 機率
        delay = random.uniform(1.0, 3.0)
        print(f"等待 {delay:.1f} 秒後開始第 {attempt}/{MAX_RETRIES} 次嘗試...")
        time.sleep(delay)

        scraper = cloudscraper.create_scraper(browser={
            'browser': 'chrome',
            'platform': 'windows',
            'desktop': True
        })

        try:
            response = scraper.get(api_url, headers=headers, timeout=30)
            response.encoding = 'utf-8'
            html = response.text

            # Cloudflare 攔截偵測
            if is_cloudflare_block(html, response.status_code):
                print(f"⚠️ [嘗試 {attempt}] 偵測到 Cloudflare 攔截（狀態碼: {response.status_code}）")
                if attempt < MAX_RETRIES:
                    print(f"   等待 {RETRY_DELAY} 秒後重試...")
                    time.sleep(RETRY_DELAY)
                continue

            if response.status_code != 200:
                print(f"⚠️ [嘗試 {attempt}] 抓取失敗，狀態碼: {response.status_code}")
                if attempt < MAX_RETRIES:
                    time.sleep(RETRY_DELAY)
                continue

            # 方法 1：Next.js Pages Router — __NEXT_DATA__
            nd_match = re.search(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', html, re.DOTALL)
            if nd_match:
                try:
                    nd = json.loads(nd_match.group(1))
                    items = nd.get('props', {}).get('pageProps', {}).get('initialItems', [])
                    if items:
                        print(f"✅ 從 __NEXT_DATA__ 解析成功，共找到 {len(items)} 筆商品。")
                        return items
                    print(f"⚠️ __NEXT_DATA__ 存在但無 initialItems。pageProps keys: {list(nd.get('props',{}).get('pageProps',{}).keys())}")
                except json.JSONDecodeError as e:
                    print(f"⚠️ __NEXT_DATA__ 解析失敗: {e}")

            # 方法 2：括弧深度追蹤直接從原始 HTML 取出
            key = '"initialItems":'
            search_start = 0
            while True:
                idx = html.find(key, search_start)
                if idx < 0:
                    break
                arr_start = idx + len(key)
                while arr_start < len(html) and html[arr_start] in ' \t\n\r':
                    arr_start += 1
                if arr_start >= len(html) or html[arr_start] != '[':
                    search_start = idx + 1
                    continue
                arr_str = extract_json_array(html, arr_start)
                if arr_str:
                    try:
                        items = json.loads(arr_str)
                        if items:
                            print(f"✅ 括弧追蹤解析成功，共找到 {len(items)} 筆商品。")
                            return items
                    except json.JSONDecodeError as e:
                        pos = e.pos
                        print(f"⚠️ 括弧追蹤取出但 JSON 仍無效，位置 {pos} 前後: {repr(arr_str[max(0,pos-60):pos+60])}")
                search_start = idx + 1

            # 方法 3：Next.js App Router RSC streaming — self.__next_f.push
            rsc_chunks = re.findall(r'self\.__next_f\.push\(\[1,"((?:[^"\\]|\\.)*)"\]\)', html)
            print(f"RSC chunks 數量: {len(rsc_chunks)}")
            for chunk in rsc_chunks:
                try:
                    decoded = json.loads(f'"{chunk}"')
                    if '"initialItems"' not in decoded:
                        continue
                    print("找到含 initialItems 的 RSC chunk，嘗試解析...")
                    idx = decoded.find(key)
                    arr_start = idx + len(key)
                    arr_str = extract_json_array(decoded, arr_start)
                    if arr_str:
                        items = json.loads(arr_str)
                        if items:
                            print(f"✅ 從 RSC chunk 解析成功，共找到 {len(items)} 筆商品。")
                            return items
                except Exception as e:
                    print(f"⚠️ RSC chunk 解析錯誤: {e}")
                    continue

            # 所有方法失敗，印出 debug 資訊
            print(f"⚠️ [嘗試 {attempt}] 所有解析方法均失敗。")
            idx = html.find('"initialItems"')
            if idx >= 0:
                print(f"找到 'initialItems' 於位置 {idx}，前後 300 字：")
                print(repr(html[idx:idx+300]))
            else:
                print("HTML 中不含 'initialItems'，前 500 字：")
                print(html[:500])

            if attempt < MAX_RETRIES:
                print(f"等待 {RETRY_DELAY} 秒後重試...")
                time.sleep(RETRY_DELAY)

        except Exception as e:
            print(f"⚠️ [嘗試 {attempt}] 發生錯誤: {e}")
            if attempt < MAX_RETRIES:
                print(f"等待 {RETRY_DELAY} 秒後重試...")
                time.sleep(RETRY_DELAY)

    print("❌ 所有重試均失敗，本次掃描中止。")
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
