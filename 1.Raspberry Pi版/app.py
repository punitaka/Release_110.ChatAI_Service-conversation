"""
app.py
======
Raspberry Pi上で動くWebインターフェース本体。

    python3 app.py

を実行すると、同じLAN内のPC・スマホのブラウザから

    http://<Raspberry PiのIPアドレス>:5000

にアクセスして、Manus / GPT(OpenAI) / Claude(Anthropic) の中から2〜3体を選び、
司会役(モデレーター)を指定して、テーマについて会話させることができる。
会話の進行(発言・思考中ステータス・エラー)はリアルタイムに表示される。

設計メモ:
  - 会話イベントの配信には Server-Sent Events (SSE) を使用している(/events)。
    WebSocketより単純で、追加ライブラリなし(Flask標準機能)で実装できるため採用。
  - 会話の生成自体は時間のかかる処理(Manusのポーリングで最大300秒)なので、
    バックグラウンドスレッドで実行し、HTTPリクエストの応答をブロックしない。
  - 同時に実行できる会話は1つまで(初期版の割り切り)。2つ目のSTARTは409を返す。
  - APIキー(MANUS_API_KEY / OPENAI_API_KEY / ANTHROPIC_API_KEY)はこのサーバーの
    プロセス内にのみ存在し、ブラウザへ送るデータ(SSEイベント)には一切含めていない。
  - 会話ログはlogs/配下に自動保存され、自動削除の仕組みはない(無期限に残る)。
    機密性の高い用途向けに、画面から一括削除できる /api/clear_logs を用意している。
"""

import json
import pathlib
import queue
import threading

from flask import Flask, Response, jsonify, render_template, request, send_from_directory

import conversation_engine as engine
from broadcaster import Broadcaster

app = Flask(__name__)
LOGS_DIR = pathlib.Path("logs")

broadcaster = Broadcaster()

_state_lock = threading.Lock()
_state = {"thread": None, "control": None}

MAX_TURNS_LIMIT = 20
MIN_PARTICIPANTS = 2
MAX_PARTICIPANTS = 3
VALID_PROVIDERS = {"manus", "openai", "claude"}


@app.route("/")
def index():
    keys_status = {
        "manus": bool(engine.MANUS_API_KEY),
        "openai": bool(engine.OPENAI_API_KEY),
        "claude": bool(engine.ANTHROPIC_API_KEY),
    }
    return render_template(
        "index.html",
        keys_status=keys_status,
        tones=engine.TONE_PRESETS,
        provider_names=engine.PROVIDER_DISPLAY_NAME,
        canonical_order=engine.CANONICAL_ORDER,
        model_choices=engine.MODEL_CHOICES,
        max_turns_limit=MAX_TURNS_LIMIT,
    )


@app.route("/download/<path:filename>")
def download(filename):
    # send_from_directory はディレクトリトラバーサル(../等)を自動的に拒否する。
    # logs/ 配下のファイルのみ、ブラウザのダウンロードとして配信する。
    return send_from_directory(LOGS_DIR, filename, as_attachment=True)


@app.route("/api/clear_logs", methods=["POST"])
def api_clear_logs():
    """logs/ 配下の会話ログ(conversation_*.json / .txt)をすべて削除する。
    経営層への貸し出し等、機密性を重視する用途向けの「即時消去」ボタン用。
    実行中の会話(サーバープロセス内のメモリ上の状態)には影響しない。"""
    LOGS_DIR.mkdir(exist_ok=True)
    deleted = 0
    errors = []
    for json_path in LOGS_DIR.glob("conversation_*.json"):
        for path in (json_path, json_path.with_suffix(".txt")):
            if path.exists():
                try:
                    path.unlink()
                    deleted += 1
                except OSError as e:
                    errors.append(f"{path.name}: {e}")

    return jsonify(status="ok", deleted=deleted, errors=errors)


@app.route("/events")
def events():
    def stream():
        q = broadcaster.subscribe()
        try:
            while True:
                try:
                    event = q.get(timeout=15)
                    yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                except queue.Empty:
                    yield ": keep-alive\n\n"
        finally:
            broadcaster.unsubscribe(q)

    return Response(stream(), mimetype="text/event-stream")


def _validate_participants(raw_participants):
    """
    raw_participants: JSONから受け取った [{"provider": "...", "model": "..."}] 形式のリスト。
    戻り値: (正規化されたparticipantsリスト, エラーメッセージ or None)
    """
    if not isinstance(raw_participants, list):
        return None, "参加AIの指定が不正です。"

    if not (MIN_PARTICIPANTS <= len(raw_participants) <= MAX_PARTICIPANTS):
        return None, f"参加AIは{MIN_PARTICIPANTS}〜{MAX_PARTICIPANTS}体で選んでください。"

    seen = set()
    normalized = []
    for item in raw_participants:
        if not isinstance(item, dict):
            return None, "参加AIの指定が不正です。"
        provider = item.get("provider")
        if provider not in VALID_PROVIDERS:
            return None, f"不明なAIが指定されました: {provider}"
        if provider in seen:
            return None, f"同じAI({provider})が重複して選択されています。"
        seen.add(provider)

        model = item.get("model")
        if provider == "manus":
            model = None  # Manusはモデル選択非対応
        else:
            if not model or not str(model).strip():
                return None, f"{provider} のモデルを指定してください。"
            model = str(model).strip()

        normalized.append({"provider": provider, "model": model})

    return normalized, None


@app.route("/api/start", methods=["POST"])
def api_start():
    data = request.get_json(silent=True) or {}

    topic = (data.get("topic") or "").strip()
    tone_key = str(data.get("tone", "")).strip()
    moderator_provider = str(data.get("moderator", "")).strip()
    try:
        max_turns = int(data.get("max_turns", 0))
    except (TypeError, ValueError):
        max_turns = 0

    if not topic:
        return jsonify(error="テーマを入力してください。"), 400
    if tone_key not in engine.TONE_PRESETS:
        return jsonify(error="トーンの指定が不正です。"), 400
    if not (1 <= max_turns <= MAX_TURNS_LIMIT):
        return jsonify(error=f"ターン数は1〜{MAX_TURNS_LIMIT}の範囲で指定してください。"), 400

    participants, err = _validate_participants(data.get("participants"))
    if err:
        return jsonify(error=err), 400

    if moderator_provider not in {p["provider"] for p in participants}:
        return jsonify(error="モデレーターは参加AIの中から選んでください。"), 400

    with _state_lock:
        running_thread = _state["thread"]
        if running_thread is not None and running_thread.is_alive():
            return jsonify(error="既に会話が実行中です。停止してから開始してください。"), 409

        control = engine.ConversationControl()
        thread = threading.Thread(
            target=engine.run_conversation,
            args=(topic, tone_key, max_turns, participants, moderator_provider, control, broadcaster),
            daemon=True,
        )
        _state["thread"] = thread
        _state["control"] = control
        thread.start()

    return jsonify(status="started"), 202


@app.route("/api/control", methods=["POST"])
def api_control():
    data = request.get_json(silent=True) or {}
    action = data.get("action")

    with _state_lock:
        control = _state["control"]
        thread = _state["thread"]

    if control is None or thread is None or not thread.is_alive():
        return jsonify(error="実行中の会話がありません。"), 409

    if action == "pause":
        control.pause()
    elif action == "resume":
        control.resume()
    elif action == "stop":
        control.stop()
    else:
        return jsonify(error="actionはpause/resume/stopのいずれかを指定してください。"), 400

    return jsonify(status="ok")


if __name__ == "__main__":
    # threaded=True: SSE接続(/events)を保持したまま、/api/start等の他のリクエストも
    # 同時に処理できるようにするために必須。
    app.run(host="0.0.0.0", port=5000, threaded=True)
