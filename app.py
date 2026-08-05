import os
import re
from datetime import datetime, timezone, timedelta
import requests
from flask import Flask, request, abort

# LINE Messaging API SDK (v3)
from linebot.v3 import WebhookHandler
from linebot.v3.exceptions import InvalidSignatureError
from linebot.v3.messaging import (
    Configuration,
    ApiClient,
    MessagingApi,
    ReplyMessageRequest,
    TextMessage
)
from linebot.v3.webhooks import MessageEvent, TextMessageContent

app = Flask(__name__)

# ==============================================================================
# 0. タイムゾーン設定（日本標準時 JST = UTC + 9時間）
# ==============================================================================
JST = timezone(timedelta(hours=9))

# ==============================================================================
# 1. 各種APIキー設定（環境変数）
# ==============================================================================
ODPT_CONSUMER_KEY = os.environ.get("ODPT_CONSUMER_KEY", "uwvu4a98yybp82h2i7w1j2s9kozuxl7p6a4yyuempfpgtdbwbf2v6z2gsj551ff8")
LINE_CHANNEL_SECRET = os.environ.get("LINE_CHANNEL_SECRET", "YOUR_LINE_CHANNEL_SECRET")
LINE_CHANNEL_ACCESS_TOKEN = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN", "YOUR_LINE_CHANNEL_ACCESS_TOKEN")

configuration = Configuration(access_token=LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)

# 列車時刻表 API（TrainTimetable）を使用
ODPT_API_URL = "https://api.odpt.org/api/v4/odpt:TrainTimetable"
RAILWAY_ID = "odpt.Railway:TokyoMetro.Namboku"
RAIL_DIRECTION_ID = "odpt.RailDirection:TokyoMetro.Meguro"

STATION_NAME_MAP = {
    "Ebina": "海老名",
    "Shonandai": "湘南台",
    "Nishiya": "西谷",
    "ShinYokohama": "新横浜",
    "Hiyoshi": "日吉",
    "MusashiKosugi": "武蔵小杉",
    "Meguro": "目黒",
    "ShirokaneTakanawa": "白金高輪",
    "Shirokanedai": "白金台",
    "Motonakayoshi": "元住吉",
    "Kikuna": "菊名"
}

# ==============================================================================
# 2. 両数・担当会社・当駅始発案内ロジック
# ==============================================================================
def analyze_car_length(train_number: str, destination_raw: str, is_origin: bool) -> dict:
    train_number = train_number.upper()
    destination_display = STATION_NAME_MAP.get(destination_raw, destination_raw)

    match = re.search(r"([A-Z])$", train_number)
    suffix = match.group(1) if match else ""

    recommendations = []
    if is_origin:
        recommendations.append("✨【当駅始発】座れる可能性大！")

    if suffix == "K":
        company = "東急電鉄"
        cars = "8両"
        recommendations.append("全列車8両編成")
    elif suffix == "G":
        company = "相鉄"
        cars = "8両"
        recommendations.append("全列車8両（ネイビーブルー車両）")
    elif suffix == "S":
        company = "埼玉高速鉄道"
        cars = "6両"
        recommendations.append("6両編成（混雑注意）")
    elif suffix == "M":
        company = "東京メトロ"
        cars = "6両 または 8両"
        recommendations.append("メトロ車（順次8両化中）")
    else:
        company = "その他"
        cars = "不明"

    sotetsu_keywords = ["海老名", "湘南台", "西谷", "相鉄", "ebina", "shonandai", "nishiya", "sotetsu"]
    search_target = f"{destination_display} {destination_raw}".lower()

    if any(kw in search_target for kw in sotetsu_keywords):
        cars = "8両"
        if "全列車8両" not in "".join(recommendations):
            recommendations.append("相鉄直通のため8両固定")

    return {
        "train_number": train_number,
        "destination": destination_display,
        "company": company,
        "cars": cars,
        "is_origin": is_origin,
        "recommendation": " / ".join(recommendations) if recommendations else "通常運行",
    }

# ==============================================================================
# 3. 列車時刻表 API から直近5本を抽出するメイン処理
# ==============================================================================
def build_timetable_message(target_time_str: str = None) -> str:
    if not ODPT_CONSUMER_KEY:
        return "⚠️ エラー: ODPT APIのアクセストークンが設定されていません。"

    params = {
        "acl:consumerKey": ODPT_CONSUMER_KEY,
        "odpt:railway": RAILWAY_ID,
        "odpt:railDirection": RAIL_DIRECTION_ID,
    }

    try:
        res = requests.get(ODPT_API_URL, params=params, timeout=10)
        res.raise_for_status()
        raw_trains = res.json()
    except Exception as e:
        return f"❌ データ取得エラーが発生しました:\n{e}"

    if not raw_trains:
        return "⚠️ 時刻表データが見つかりませんでした。"

    now_jst = datetime.now(JST)
    now_str = target_time_str if target_time_str else now_jst.strftime("%H:%M")
    is_weekend = now_jst.weekday() >= 5
    target_calendar = "SaturdayHoliday" if is_weekend else "Weekday"

    # カレンダー条件に合う列車をフィルタ
    trains_today = [
        t for t in raw_trains
        if target_calendar in t.get("odpt:calendar", "")
    ]

    upcoming_trains = []

    for train in trains_today:
        tt_objects = train.get("odpt:trainTimetableObject", [])
        
        # 赤羽岩淵駅の発車時刻を探す
        akabane_dep_time = None
        for obj in tt_objects:
            dep_station = obj.get("odpt:departureStation", "")
            if "AkabaneIwabuchi" in dep_station:
                akabane_dep_time = obj.get("odpt:departureTime", "")
                break

        # 赤羽岩淵の発車予定があり、指定時刻以降のもの
        if akabane_dep_time and akabane_dep_time >= now_str:
            train_num = train.get("odpt:trainNumber", "")
            
            # 行き先を取得
            dest_list = train.get("odpt:destinationStation", [])
            if dest_list:
                dest_raw = dest_list[0].split(".")[-1]
            else:
                dest_raw = tt_objects[-1].get("odpt:arrivalStation", "").split(".")[-1] if tt_objects else ""

            # 当駅始発の確定判定（列車全体の始発駅に AkabaneIwabuchi が含まれるか）
            origin_list = train.get("odpt:originStation", [])
            is_origin = any("akabaneiwabuchi" in str(o).lower() for o in origin_list)

            eval_res = analyze_car_length(train_num, dest_raw, is_origin)
            eval_res["departure_time"] = akabane_dep_time
            upcoming_trains.append(eval_res)

    # 発車時刻順に並び替え
    upcoming_trains.sort(key=lambda x: x["departure_time"])
    selected_trains = upcoming_trains[:5]

    if not selected_trains:
        return f"🚃 赤羽岩淵発（目黒方面）\n時刻 ({now_str}) 以降の発車予定はありません。"

    lines = [
        "🚃 赤羽岩淵発（目黒方面）両数案内",
        f"⏰ 検索基準時刻: {now_str}\n"
    ]

    for t in selected_trains:
        origin_tag = " 🪑[当駅始発]" if t["is_origin"] else ""
        lines.append(f"🕒 {t['departure_time']}発【{t['destination']} 行】{origin_tag}")
        lines.append(f" ├ 編成: {t['cars']}")
        lines.append(f" ├ 車両: {t['company']} ({t['train_number']})")
        lines.append(f" └ {t['recommendation']}")
        lines.append("-" * 20)

    return "\n".join(lines)

# ==============================================================================
# 4. 入力文字（時刻）のパース処理
# ==============================================================================
def parse_input(user_text: str):
    m = re.search(r"(\d{1,2}):(\d{2})", user_text)
    if m:
        h, min_val = int(m.group(1)), int(m.group(2))
        return f"{h:02d}:{min_val:02d}"
    
    m2 = re.search(r"(\d{1,2})\s*時\s*(\d{1,2})?", user_text)
    if m2:
        h = int(m2.group(1))
        min_val = int(m2.group(2)) if m2.group(2) else 0
        return f"{h:02d}:{min_val:02d}"
        
    m3 = re.search(r"^\s*(\d{1,2})\s*$", user_text.strip())
    if m3:
        h = int(m3.group(1))
        if 0 <= h <= 23:
            return f"{h:02d}:00"

    return None

# ==============================================================================
# 5. LINE Webhook サーバー処理
# ==============================================================================
@app.route("/callback", methods=['POST'])
def callback():
    signature = request.headers.get('X-Line-Signature', '')
    body = request.get_data(as_text=True)

    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)

    return 'OK'

@handler.add(MessageEvent, message=TextMessageContent)
def handle_message(event):
    try:
        user_text = event.message.text
        target_time = parse_input(user_text)
        reply_text = build_timetable_message(target_time_str=target_time)
    except Exception as e:
        reply_text = f"⚠️ エラーが発生しました:\n{e}"

    with ApiClient(configuration) as api_client:
        line_bot_api = MessagingApi(api_client)
        line_bot_api.reply_message_with_http_info(
            ReplyMessageRequest(
                reply_token=event.reply_token,
                messages=[TextMessage(text=reply_text)]
            )
        )

# ==============================================================================
# 6. ローカルテスト実行
# ==============================================================================
if __name__ == "__main__":
    print("=== 🧪 11:00 のテスト実行 ===")
    print(build_timetable_message(target_time_str="11:00"))
