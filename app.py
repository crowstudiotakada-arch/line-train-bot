import os
import re
from math import radians, cos, sin, asin, sqrt
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
    TextMessage,
    QuickReply,
    QuickReplyItem,
    LocationAction
)
from linebot.v3.webhooks import MessageEvent, TextMessageContent, LocationMessageContent

app = Flask(__name__)

# ==============================================================================
# 0. タイムゾーン設定（JST）& ユーザー状態保持
# ==============================================================================
JST = timezone(timedelta(hours=9))
user_last_station = {}  # { user_id: station_info }

# ガイドメッセージ
HELP_MESSAGE = (
    "📖【南北線案内Botの使い方】\n\n"
    "① 駅名で検索する\n"
    "   「王子」「飯田橋」などの駅名を送信すると、その駅の最新時刻表と【待っている電車のリアルタイム現在地】を表示します。\n"
    "   例：「溜池山王 18:30」のように時間指定も可能です。\n\n"
    "② リッチメニューで一発表示\n"
    "   ・🏠 赤羽岩淵(上り)：赤羽岩淵の目黒方面を表示\n"
    "   ・🏢 溜池山王(下り)：溜池山王の浦和美園方面を表示\n"
    "   ・🚃 目黒方面/浦和美園方面：選択中の駅のまま方向を切り替え\n\n"
    "③ 位置情報から最寄り駅を検索\n"
    "   「📍 現在地から検索」を押すと表示されるボタンから位置情報を送信すると、一番近い南北線の駅を自動検索します。\n\n"
    "💡【表示マークの見方】\n"
    "📍 現在地：待っている電車が今どの区間を走っているかリアルタイム表示\n"
    "🪑[当駅始発]：座れる可能性が高い始発電車です。\n"
    "編成/車両：6両・8両や運行会社（東急・相鉄・メトロ等）を表示します。\n\n"
    "⚠️【応答に時間がかかる場合】\n"
    "サーバーが休止状態（スリープ）の場合、初回の返信に15〜30秒ほどお時間をいただくことがあります。\n"
    "反応がない場合は、お手数ですが1分後にもう一度送信・タップしてみてください！"
)

# ==============================================================================
# 1. 各種APIキー設定（環境変数）
# ==============================================================================
ODPT_CONSUMER_KEY = os.environ.get("ODPT_CONSUMER_KEY", "uwvu4a98yybp82h2i7w1j2s9kozuxl7p6a4yyuempfpgtdbwbf2v6z2gsj551ff8")
LINE_CHANNEL_SECRET = os.environ.get("LINE_CHANNEL_SECRET", "YOUR_LINE_CHANNEL_SECRET")
LINE_CHANNEL_ACCESS_TOKEN = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN", "YOUR_LINE_CHANNEL_ACCESS_TOKEN")

configuration = Configuration(access_token=LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)

# API エンドポイント
ODPT_STATION_TIMETABLE_URL = "https://api.odpt.org/api/v4/odpt:StationTimetable"
ODPT_TRAIN_TIMETABLE_URL = "https://api.odpt.org/api/v4/odpt:TrainTimetable"
ODPT_TRAIN_INFO_URL = "https://api.odpt.org/api/v4/odpt:TrainInformation"
ODPT_TRAIN_LOCATION_URL = "https://api.odpt.org/api/v4/odpt:Train"
RAILWAY_ID = "odpt.Railway:TokyoMetro.Namboku"

# ==============================================================================
# 2. 東京メトロ南北線 全19駅 マスター
# ==============================================================================
STATIONS_GEO = [
    {"name": "目黒", "id": "odpt.Station:TokyoMetro.Namboku.Meguro", "lat": 35.6340, "lon": 139.7158, "aliases": ["目黒", "めぐろ"]},
    {"name": "白金台", "id": "odpt.Station:TokyoMetro.Namboku.Shirokanedai", "lat": 35.6379, "lon": 139.7258, "aliases": ["白金台", "しろかねだい"]},
    {"name": "白金高輪", "id": "odpt.Station:TokyoMetro.Namboku.ShirokaneTakanawa", "lat": 35.6429, "lon": 139.7340, "aliases": ["白金高輪", "しろかねたかなわ"]},
    {"name": "麻布十番", "id": "odpt.Station:TokyoMetro.Namboku.AzabuJuban", "lat": 35.6546, "lon": 139.7371, "aliases": ["麻布十番", "あざぶじゅうばん"]},
    {"name": "六本木一丁目", "id": "odpt.Station:TokyoMetro.Namboku.RoppongiItchome", "lat": 35.6656, "lon": 139.7390, "aliases": ["六本木一丁目", "ろっぽんぎいっちょうめ"]},
    {"name": "溜池山王", "id": "odpt.Station:TokyoMetro.Namboku.TameikeSanno", "lat": 35.6735, "lon": 139.7417, "aliases": ["溜池山王", "ためいけさんのう"]},
    {"name": "永田町", "id": "odpt.Station:TokyoMetro.Namboku.Nagatacho", "lat": 35.6787, "lon": 139.7402, "aliases": ["永田町", "ながたちょう"]},
    {"name": "四ツ谷", "id": "odpt.Station:TokyoMetro.Namboku.Yotsuya", "lat": 35.6860, "lon": 139.7306, "aliases": ["四ツ谷", "四谷", "よつや"]},
    {"name": "市ケ谷", "id": "odpt.Station:TokyoMetro.Namboku.Ichigaya", "lat": 35.6912, "lon": 139.7357, "aliases": ["市ケ谷", "市ヶ谷", "いちがや"]},
    {"name": "飯田橋", "id": "odpt.Station:TokyoMetro.Namboku.Iidabashi", "lat": 35.7021, "lon": 139.7450, "aliases": ["飯田橋", "いいだばし"]},
    {"name": "後楽園", "id": "odpt.Station:TokyoMetro.Namboku.Korakuen", "lat": 35.7078, "lon": 139.7518, "aliases": ["後楽園", "こうらくえん"]},
    {"name": "東大前", "id": "odpt.Station:TokyoMetro.Namboku.Todaimae", "lat": 35.7176, "lon": 139.7546, "aliases": ["東大前", "とうだいまえ"]},
    {"name": "本駒込", "id": "odpt.Station:TokyoMetro.Namboku.HonKomagome", "lat": 35.7243, "lon": 139.7540, "aliases": ["本駒込", "ほんこまごめ"]},
    {"name": "駒込", "id": "odpt.Station:TokyoMetro.Namboku.Komagome", "lat": 35.7365, "lon": 139.7470, "aliases": ["駒込", "こまごめ"]},
    {"name": "西ヶ原", "id": "odpt.Station:TokyoMetro.Namboku.Nishigahara", "lat": 35.7456, "lon": 139.7420, "aliases": ["西ヶ原", "西ケ原", "にしがはら"]},
    {"name": "王子", "id": "odpt.Station:TokyoMetro.Namboku.Oji", "lat": 35.7525, "lon": 139.7380, "aliases": ["王子", "おうじ"]},
    {"name": "王子神谷", "id": "odpt.Station:TokyoMetro.Namboku.OjiKamiya", "lat": 35.7651, "lon": 139.7351, "aliases": ["王子神谷", "おうじかみや"]},
    {"name": "志茂", "id": "odpt.Station:TokyoMetro.Namboku.Shimo", "lat": 35.7779, "lon": 139.7326, "aliases": ["志茂", "しも"]},
    {"name": "赤羽岩淵", "id": "odpt.Station:TokyoMetro.Namboku.AkabaneIwabuchi", "lat": 35.7836, "lon": 139.7214, "aliases": ["赤羽岩淵", "赤羽", "あかばねいわぶち"]},
]

STATION_ID_TO_NAME = {st["id"]: st["name"] for st in STATIONS_GEO}

STATION_NAME_MAP = {
    # 目黒方面
    "Ebina": "海老名", "Shonandai": "湘南台", "Nishiya": "西谷", "ShinYokohama": "新横浜",
    "Hiyoshi": "日吉", "MusashiKosugi": "武蔵小杉", "Meguro": "目黒", "ShirokaneTakanawa": "白金高輪",
    "Shirokanedai": "白金台", "Motonakayoshi": "元住吉", "Kikuna": "菊名",
    # 浦和美園方面
    "AkabaneIwabuchi": "赤羽岩淵", "Hatogaya": "鳩ヶ谷", "UrawaMisono": "浦和美園",
    "OjiKamiya": "王子神谷", "Komagome": "駒込"
}

# ==============================================================================
# 3. 時刻・日付計算（朝4時基準の24時間管理）
# ==============================================================================
def time_to_minutes(time_str: str) -> int:
    """HH:MM を朝4時起算の通算分数に変換 (0:15 -> 24:15 -> 1455分)"""
    try:
        parts = time_str.split(":")
        h = int(parts[0])
        m = int(parts[1])
        if h < 4:
            h += 24
        return h * 60 + m
    except Exception:
        return 0

def get_current_time_info(target_time_str: str = None):
    """朝4時起算の基準分数・表示用文字列・日付判定（平日/土休日）を取得"""
    now_jst = datetime.now(JST)

    if target_time_str:
        try:
            parts = target_time_str.split(":")
            h = int(parts[0])
            m = int(parts[1])
        except Exception:
            h, m = now_jst.hour, now_jst.minute
            target_time_str = f"{h:02d}:{m:02d}"

        # 手入力された時刻が深夜0〜3時台の場合は前日ダイヤとして判定
        if h < 4:
            effective_dt = now_jst if now_jst.hour < 4 else (now_jst - timedelta(days=1))
        else:
            effective_dt = now_jst

        adj_minutes = (h + 24 if h < 4 else h) * 60 + m
        display_str = target_time_str
    else:
        h, m = now_jst.hour, now_jst.minute
        if h < 4:
            effective_dt = now_jst - timedelta(days=1)
            adj_minutes = (h + 24) * 60 + m
        else:
            effective_dt = now_jst
            adj_minutes = h * 60 + m
        display_str = f"{h:02d}:{m:02d}"

    is_weekend = effective_dt.weekday() >= 5
    calendar_key = "odpt.Calendar:SaturdayHoliday" if is_weekend else "odpt.Calendar:Weekday"

    return adj_minutes, display_str, calendar_key

def calculate_distance_km(lat1, lon1, lat2, lon2):
    R = 6371.0
    dlat = radians(lat2 - lat1)
    dlon = radians(lon2 - lon1)
    a = sin(dlat / 2)**2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon / 2)**2
    c = 2 * asin(sqrt(a))
    return R * c

def find_nearest_station(user_lat, user_lon):
    closest_station = None
    min_dist_km = float('inf')
    for st in STATIONS_GEO:
        dist = calculate_distance_km(user_lat, user_lon, st["lat"], st["lon"])
        if dist < min_dist_km:
            min_dist_km = dist
            closest_station = st
    return closest_station, int(min_dist_km * 1000)

def find_station_by_text(user_text: str):
    cleaned_text = re.sub(r'(目黒方面|浦和美園方面|赤羽岩淵方面|上り|下り|現在地)', '', user_text).strip()
    if not cleaned_text:
        return None

    matches = []
    for st in STATIONS_GEO:
        for alias in st["aliases"]:
            if alias in cleaned_text:
                matches.append((len(alias), st))
    if matches:
        matches.sort(key=lambda x: x[0], reverse=True)
        return matches[0][1]
    return None

def parse_direction(user_text: str) -> str:
    if any(kw in user_text for kw in ["目黒方面", "上り", "目黒行"]):
        return "MEGURO"
    if any(kw in user_text for kw in ["浦和美園方面", "下り", "美園行", "赤羽岩淵方面", "浦和美園"]):
        return "URAWA"

    urawa_keywords = ["浦和美園", "美園", "鳩ヶ谷", "埼玉高速"]
    for kw in urawa_keywords:
        if kw in user_text:
            return "URAWA"
    return "MEGURO"

def parse_time_input(user_text: str):
    m = re.search(r"(\d{1,2}):(\d{2})", user_text)
    if m:
        return f"{int(m.group(1)):02d}:{int(m.group(2)):02d}"
    m2 = re.search(r"(\d{1,2})\s*時\s*(\d{1,2})?", user_text)
    if m2:
        h = int(m2.group(1))
        min_val = int(m2.group(2)) if m2.group(2) else 0
        return f"{h:02d}:{min_val:02d}"
    return None

# ==============================================================================
# 4. 運行障害・遅延情報 & リアルタイム列車位置 照合辞書取得
# ==============================================================================
def fetch_train_information() -> str:
    params = {
        "acl:consumerKey": ODPT_CONSUMER_KEY,
        "odpt:railway": RAILWAY_ID,
    }
    try:
        res = requests.get(ODPT_TRAIN_INFO_URL, params=params, timeout=5)
        if res.status_code == 200 and res.json():
            info = res.json()[0]
            text_dict = info.get("odpt:trainInformationText", {})
            ja_text = text_dict.get("ja", "平常通り運行しています。")
            return ja_text
    except Exception:
        pass
    return "運行情報の取得に失敗しました。"

def fetch_realtime_train_map() -> tuple[dict, bool]:
    params = {
        "acl:consumerKey": ODPT_CONSUMER_KEY,
        "odpt:railway": RAILWAY_ID,
    }
    train_map = {}
    is_active = False
    try:
        res = requests.get(ODPT_TRAIN_LOCATION_URL, params=params, timeout=5)
        if res.status_code == 200 and isinstance(res.json(), list):
            raw_list = res.json()
            if len(raw_list) > 0:
                is_active = True
                for t in raw_list:
                    t_num = str(t.get("odpt:trainNumber", "")).upper().strip()
                    if not t_num:
                        continue

                    from_st_id = t.get("odpt:fromStation", "") or ""
                    to_st_id = t.get("odpt:toStation", "") or ""
                    delay_sec = t.get("odpt:delay", 0) or 0

                    from_name = STATION_ID_TO_NAME.get(from_st_id, "")
                    if not from_name and from_st_id:
                        from_name = from_st_id.split(".")[-1]

                    to_name = STATION_ID_TO_NAME.get(to_st_id, "")
                    if not to_name and to_st_id:
                        to_name = to_st_id.split(".")[-1]

                    if to_name and to_name != from_name:
                        loc_str = f"{from_name} ➔ {to_name} (走行中)"
                    elif from_name:
                        loc_str = f"{from_name} (停車中)"
                    else:
                        loc_str = "走行中"

                    if delay_sec >= 60:
                        loc_str += f" ⚠️{delay_sec//60}分遅れ"

                    digits = "".join(re.findall(r'\d+', t_num))

                    train_map[t_num] = loc_str
                    train_map[re.sub(r'^[AB]', '', t_num)] = loc_str
                    if digits:
                        train_map[digits] = loc_str
    except Exception as e:
        print(f"[ERROR] fetch_realtime_train_map: {e}")
    return train_map, is_active

# ==============================================================================
# 5. 両数・編成判定ロジック
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
# 6. 時刻表取得＆ピンポイント位置紐付けロジック（深夜24時台対応）
# ==============================================================================
def get_origin_train_numbers(target_station_id: str) -> set:
    params = {
        "acl:consumerKey": ODPT_CONSUMER_KEY,
        "odpt:railway": RAILWAY_ID,
    }
    origin_set = set()
    try:
        res = requests.get(ODPT_TRAIN_TIMETABLE_URL, params=params, timeout=8)
        if res.status_code == 200:
            station_short = target_station_id.split(".")[-1].lower()
            for train in res.json():
                orig_list = train.get("odpt:originStation", [])
                if any(station_short in str(o).lower() for o in orig_list):
                    t_num = train.get("odpt:trainNumber", "").upper()
                    if t_num:
                        origin_set.add(t_num)
    except Exception:
        pass
    return origin_set

def build_timetable_message(station_info: dict = None, target_time_str: str = None, direction_key: str = "MEGURO") -> str:
    if not ODPT_CONSUMER_KEY:
        return "⚠️ エラー: ODPT APIのアクセストークンが設定されていません。"

    if not station_info:
        station_info = STATIONS_GEO[-1]  # デフォルト: 赤羽岩淵

    target_station_name = station_info["name"]
    target_station_id = station_info["id"]

    direction_title = "目黒方面(上り)" if direction_key == "MEGURO" else "赤羽岩淵・浦和美園方面(下り)"

    # 朝4時起算の基準分数、表示用時刻、カレンダー（平日/土休日）を取得
    now_adj_minutes, now_display_str, target_calendar = get_current_time_info(target_time_str)

    # 1. 運行情報＆リアルタイム列車位置マップを取得
    train_info_text = fetch_train_information()
    realtime_map, is_realtime_active = fetch_realtime_train_map()

    info_header = ""
    if "平常通り" not in train_info_text and "平常運転" not in train_info_text:
        info_header = f"🚨【遅延・障害情報】\n{train_info_text}\n" + "─" * 20 + "\n"

    # 2. 始発電車番号リストを取得
    origin_train_numbers = get_origin_train_numbers(target_station_id)

    # 3. 駅発車時刻表を取得
    params_st = {
        "acl:consumerKey": ODPT_CONSUMER_KEY,
        "odpt:station": target_station_id,
    }

    try:
        res = requests.get(ODPT_STATION_TIMETABLE_URL, params=params_st, timeout=10)
        res.raise_for_status()
        raw_data = res.json()
    except Exception as e:
        return f"❌ データ取得エラーが発生しました:\n{e}"

    if not raw_data:
        return "⚠️ 時刻表データが見つかりませんでした。"

    matched_entry = None
    for item in raw_data:
        if item.get("odpt:calendar") == target_calendar:
            rail_dir = item.get("odpt:railDirection", "")
            if direction_key == "MEGURO" and "Meguro" in rail_dir:
                matched_entry = item
                break
            elif direction_key == "URAWA" and "Meguro" not in rail_dir:
                matched_entry = item
                break

    if not matched_entry:
        return f"{info_header}🚃 {target_station_name}駅発（{direction_title}）\n該当する方向の時刻表データが存在しません。"

    timetable_objects = matched_entry.get("odpt:stationTimetableObject", [])

    upcoming_trains = []
    for train in timetable_objects:
        dep_time = train.get("odpt:departureTime", "")
        dep_minutes = time_to_minutes(dep_time)

        # 朝4時起算の分数で比較（23:50 -> 1430分, 0:15 -> 1455分）
        if dep_minutes >= now_adj_minutes:
            train_num = train.get("odpt:trainNumber", "").upper().strip()
            dest_list = train.get("odpt:destinationStation", [])
            dest_raw = dest_list[0].split(".")[-1] if dest_list else ""

            # 当駅始発判定
            origin_list = train.get("odpt:originStation", [])
            station_short = target_station_id.split(".")[-1].lower()
            is_origin_st = any(station_short in str(o).lower() for o in origin_list)
            is_origin = is_origin_st or (train_num in origin_train_numbers)

            eval_res = analyze_car_length(train_num, dest_raw, is_origin)
            eval_res["departure_time"] = dep_time
            eval_res["dep_minutes"] = dep_minutes

            digits = "".join(re.findall(r'\d+', train_num))
            no_prefix = re.sub(r'^[AB]', '', train_num)

            loc = (
                realtime_map.get(train_num) or 
                realtime_map.get(no_prefix) or 
                realtime_map.get(digits)
            )
            eval_res["current_loc"] = loc
            upcoming_trains.append(eval_res)

    # 分数順に正しくソート
    upcoming_trains.sort(key=lambda x: x["dep_minutes"])
    selected_trains = upcoming_trains[:5]

    if not selected_trains:
        return f"{info_header}🚃 {target_station_name}駅発【{direction_title}】\n時刻 ({now_display_str}) 以降の本日の発車予定はありません（本日の運行は終了しました）。"

    lines = [info_header] if info_header else []
    lines.extend([
        f"🚃 {target_station_name}駅発【{direction_title}】発車案内",
        f"⏰ 基準時刻: {now_display_str}\n"
    ])

    for t in selected_trains:
        origin_tag = " 🪑[当駅始発]" if t["is_origin"] else ""
        lines.append(f"🕒 {t['departure_time']}発【{t['destination']} 行】{origin_tag}")
        
        if t["current_loc"]:
            lines.append(f" ├ 📍 現在地: {t['current_loc']}")

        lines.append(f" ├ 編成: {t['cars']}")
        lines.append(f" ├ 車両: {t['company']} ({t['train_number']})")
        lines.append(f" └ {t['recommendation']}")
        lines.append("-" * 20)

    if not is_realtime_active:
        lines.append("※現在メトロAPIからリアルタイム位置が配信されていない時間帯のため、予定時刻表を表示しています。")

    return "\n".join(lines)

# ==============================================================================
# 7. LINE Webhook サーバー処理
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
    user_text = event.message.text.strip()
    user_id = getattr(event.source, 'user_id', None)
    
    # 🧪 デバッグコマンド
    if "デバッグ" in user_text:
        params = {
            "acl:consumerKey": ODPT_CONSUMER_KEY,
            "odpt:railway": RAILWAY_ID,
        }
        try:
            res = requests.get(ODPT_TRAIN_LOCATION_URL, params=params, timeout=5)
            if res.status_code == 200 and isinstance(res.json(), list):
                raw_trains = res.json()
                summary_lines = []
                for t in raw_trains:
                    t_num = t.get("odpt:trainNumber", "不明")
                    from_st = str(t.get("odpt:fromStation", "")).split(".")[-1]
                    to_st = str(t.get("odpt:toStation", "")).split(".")[-1]
                    summary_lines.append(f"・{t_num}: {from_st}➔{to_st}")
                
                reply_text = (
                    f"🧪【ODPT リアルタイム位置 デバッグ】\n"
                    f"HTTPステータス: 200 OK\n"
                    f"現在走行中の電車数: {len(raw_trains)}本\n\n"
                    f"▼ 走行中電車一覧:\n" + ("\n".join(summary_lines[:10]) if summary_lines else "なし（深夜配信停止中など）")
                )
            else:
                reply_text = f"🧪【デバッグ】API応答エラー: Status {res.status_code}\n{res.text[:200]}"
        except Exception as e:
            reply_text = f"🧪【デバッグ】例外エラー発生: {e}"
        
        reply_message = TextMessage(text=reply_text)

    # 1. 位置情報送信要求判定
    elif "現在地" in user_text:
        reply_message = TextMessage(
            text="📍 下のボタンをタップして位置情報を送信してください。",
            quick_reply=QuickReply(
                items=[
                    QuickReplyItem(
                        action=LocationAction(label="📍 位置情報を送信")
                    )
                ]
            )
        )
    # 2. 運行情報・遅延情報判定
    elif any(kw in user_text for kw in ["運行情報", "遅延", "遅れ", "運行状況"]):
        info_text = fetch_train_information()
        reply_message = TextMessage(text=f"📢【南北線 運行情報】\n\n{info_text}")

    # 3. 「使い方」「ヘルプ」判定
    elif any(kw in user_text for kw in ["使い方", "つかいかた", "ヘルプ", "help", "ガイド"]):
        reply_message = TextMessage(text=HELP_MESSAGE)

    # 4. 通常の駅時刻表検索
    else:
        matched_station = find_station_by_text(user_text)
        
        if matched_station:
            if user_id:
                user_last_station[user_id] = matched_station
        else:
            if user_id and user_id in user_last_station:
                matched_station = user_last_station[user_id]

        target_time = parse_time_input(user_text)
        direction_key = parse_direction(user_text)

        reply_text = build_timetable_message(
            station_info=matched_station,
            target_time_str=target_time,
            direction_key=direction_key
        )
        reply_message = TextMessage(text=reply_text)

    with ApiClient(configuration) as api_client:
        line_bot_api = MessagingApi(api_client)
        line_bot_api.reply_message_with_http_info(
            ReplyMessageRequest(
                reply_token=event.reply_token,
                messages=[reply_message]
            )
        )

@handler.add(MessageEvent, message=LocationMessageContent)
def handle_location(event):
    user_id = getattr(event.source, 'user_id', None)
    user_lat = event.message.latitude
    user_lon = event.message.longitude

    nearest_station, dist_m = find_nearest_station(user_lat, user_lon)
    
    if user_id:
        user_last_station[user_id] = nearest_station

    header = f"📍 位置情報を受信しました！\n最寄りの南北線駅: **{nearest_station['name']}駅** (約 {dist_m}m)\n\n"
    body_text = build_timetable_message(station_info=nearest_station, direction_key="MEGURO")
    
    reply_text = header + body_text

    with ApiClient(configuration) as api_client:
        line_bot_api = MessagingApi(api_client)
        line_bot_api.reply_message_with_http_info(
            ReplyMessageRequest(
                reply_token=event.reply_token,
                messages=[
                    TextMessage(text=reply_text)
                ]
            )
        )

if __name__ == "__main__":
    print("=== 🧪 ローカルテスト ===")
    print(build_timetable_message(target_time_str="11:00"))
