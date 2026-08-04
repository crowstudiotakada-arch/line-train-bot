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
# 1. 各種APIキー設定（環境変数または直接入力）
# ==============================================================================
ODPT_CONSUMER_KEY = os.environ.get("ODPT_CONSUMER_KEY", "uwvu4a98yybp82h2i7w1j2s9kozuxl7p6a4yyuempfpgtdbwbf2v6z2gsj551ff8")
LINE_CHANNEL_SECRET = os.environ.get("LINE_CHANNEL_SECRET", "YOUR_LINE_CHANNEL_SECRET")
LINE_CHANNEL_ACCESS_TOKEN = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN", "YOUR_LINE_CHANNEL_ACCESS_TOKEN")

configuration = Configuration(access_token=LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)

# ==============================================================================
# 2. 定数・駅名マッピング定義
# ==============================================================================
ODPT_API_URL = "https://api.odpt.org/api/v4/odpt:StationTimetable"
STATION_ID = "odpt.Station:TokyoMetro.Namboku.AkabaneIwabuchi"
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
# 3. 両数・担当会社・当駅始発判定ロジック
# ==============================================================================
def analyze_car_length(train_number: str, destination_raw: str, is_origin: bool) -> dict:
    """列車番号・行き先・始発フラグから編成両数（8両/6両）と案内を判定"""
    train_number = train_number.upper()
    destination_display = STATION_NAME_MAP.get(destination_raw, destination_raw)

    match = re.search(r"([A-Z])$", train_number)
    suffix = match.group(1) if match else ""

    cars = "不明"
    company = "不明"
    recommendations = []

    # 当駅始発の場合の特別注記
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
# 4. 時刻表データ取得＆LINE用返信テキスト生成
# ==============================================================================
def build_timetable_message() -> str:
    """ODPT APIから時刻表を取得し、当駅始発判定を含めた最新の発車案内を作成"""
    if not ODPT_CONSUMER_KEY or "ここに" in ODPT_CONSUMER_KEY:
        return "⚠️ エラー: ODPT APIのアクセストークンが設定されていません。"

    params = {
        "acl:consumerKey": ODPT_CONSUMER_KEY,
        "odpt:station": STATION_ID,
        "odpt:railDirection": RAIL_DIRECTION_ID,
    }

    try:
        res = requests.get(ODPT_API_URL, params=params, timeout=10)
        res.raise_for_status()
        raw_data = res.json()
    except Exception as e:
        return f"❌ データ取得エラーが発生しました:\n{e}"

    if not raw_data:
        return "⚠️ 時刻表データが見つかりませんでした。"

    # 日本時間（JST）で現在時刻を取得
    now_jst = datetime.now(JST)
    now_str = now_jst.strftime("%H:%M")
    is_weekend = now_jst.weekday() >= 5

    target_calendar = "odpt.Calendar:SaturdayHoliday" if is_weekend else "odpt.Calendar:Weekday"

    matched_entry = next(
        (item for item in raw_data if item.get("odpt:calendar") == target_calendar),
        raw_data[0] if raw_data else None,
    )

    if not matched_entry:
        return "⚠️ 本日の時刻表データが存在しません。"

    timetable_objects = matched_entry.get("odpt:stationTimetableObject", [])

    upcoming_trains = []
    for train in timetable_objects:
        dep_time = train.get("odpt:departureTime", "")
        if dep_time >= now_str:
            train_num = train.get("odpt:trainNumber", "")
            dest_list = train.get("odpt:destinationStation", [])
            dest_raw = dest_list[0].split(".")[-1] if dest_list else ""

            # --- 当駅始発の判定 ---
            origin_list = train.get("odpt:originStation", [])
            # 始発駅データに「AkabaneIwabuchi」が含まれているか判定
            is_origin = any("AkabaneIwabuchi" in orig for orig in origin_list)

            eval_res = analyze_car_length(train_num, dest_raw, is_origin)
            eval_res["departure_time"] = dep_time
            upcoming_trains.append(eval_res)

    upcoming_trains.sort(key=lambda x: x["departure_time"])
    selected_trains = upcoming_trains[:5]

    if not selected_trains:
        return f"🚃 赤羽岩淵発（目黒方面）\n現在時刻 ({now_str}) 以降の本日の発車予定はありません（終電終了）。"

    lines = [
        "🚃 赤羽岩淵発（目黒方面）両数案内",
        f"⏰ 現在時刻: {now_str} (日本時間)\n"
    ]

    for t in selected_trains:
        # 始発列車には 🪑 マークを表示
        origin_tag = " 🪑[当駅始発]" if t["is_origin"] else ""
        lines.append(f"🕒 {t['departure_time']}発【{t['destination']} 行】{origin_tag}")
        lines.append(f" ├ 編成: {t['cars']}")
        lines.append(f" ├ 車両: {t['company']} ({t['train_number']})")
        lines.append(f" └ {t['recommendation']}")
        lines.append("-" * 20)

    return "\n".join(lines)

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
    reply_text = build_timetable_message()

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
    print("=== 🧪 ローカルテスト実行（当駅始発対応版） ===")
    print(build_timetable_message())
