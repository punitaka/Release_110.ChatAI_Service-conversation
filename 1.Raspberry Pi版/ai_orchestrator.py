"""
ai_orchestrator.py  v1.6
========================
Manus API v2 と OpenAI API を使って、AI同士がターン制で会話するオーケストレーター。
Raspberry Pi のコンソールで動作する最小構成版。

会話モード:
  1. 真面目モード  - 論理的・建設的な議論スタイル
  2. フレンドリーモード - 相手を褒め合いながら楽しく会話するスタイル

変更履歴:
  v1.6 - Manus 応答取得をメッセージ数カウント方式に変更（タイムスタンプ依存を廃止）
         API 接続エラー時の自動リトライ機能を追加（最大3回、10秒待機）
  v1.5 - タイムスタンプ絞り込み方式に変更（created_at 依存で不安定だったため v1.6 で廃止）
  v1.4 - 真面目/フレンドリーの会話モード選択機能を追加
  v1.3 - ログを logs/ ディレクトリに保存するよう変更
  v1.2 - Manus が同じ応答を繰り返すバグを修正（limit=1 方式）

使い方:
  1. .env ファイルに MANUS_API_KEY と OPENAI_API_KEY を記載する
  2. python3 ai_orchestrator.py を実行する
"""

import os
import time
import json
import datetime
import pathlib
import requests
from openai import OpenAI
from dotenv import load_dotenv

# .env ファイルが存在すれば読み込む（なければ環境変数を直接参照）
load_dotenv()

# ─────────────────────────────────────────────
# 設定
# ─────────────────────────────────────────────
MANUS_API_KEY  = os.environ.get("MANUS_API_KEY")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")

MANUS_API_BASE   = "https://api.manus.ai/v2"
OPENAI_MODEL     = "gpt-4o-mini"   # コストを抑えるために mini を使用
POLL_INTERVAL    = 5               # Manus ポーリング間隔（秒）
POLL_TIMEOUT     = 300             # Manus 最大待機時間（秒）
API_RETRY_COUNT  = 3               # API 接続エラー時のリトライ回数
API_RETRY_WAIT   = 10              # リトライ待機時間（秒）

# ─────────────────────────────────────────────
# 会話モード定義
# ─────────────────────────────────────────────
CONVERSATION_MODES = {
    "1": {
        "name": "真面目モード",
        "description": "論理的・建設的な議論スタイル",
        "manus_sys": (
            "あなたは「Manus」というAIエージェントです。現在「GPT」という別のAIと対話しています。\n"
            "相手の意見を踏まえ、論理的かつ簡潔に自分の意見を述べてください（200字以内を目安）。\n"
            "会話を繋げるため、最後に必ず相手への質問を1つ投げかけてください。"
        ),
        "gpt_sys": (
            "あなたは「GPT」というAIモデルです。現在「Manus」という別のAIと対話しています。\n"
            "相手の意見に対して、異なる視点や追加の洞察を提供してください（200字以内を目安）。\n"
            "ただ同意するだけでなく、建設的な反論や別の角度からの質問を展開してください。"
        ),
        "manus_turn_prefix": "以下の相手（GPT）の発言に対して、応答してください。\n\n相手の発言:\n",
        "gpt_turn_prefix":   "以下の相手（Manus）の発言に対して、反論や深掘りを行ってください。\n\n相手の発言:\n",
    },
    "2": {
        "name": "フレンドリーモード",
        "description": "相手を認め褒め合いながら楽しく会話するスタイル",
        "manus_sys": (
            "あなたは「Manus」というAIエージェントです。現在「GPT」という親友のAIと楽しくおしゃべりしています。\n"
            "まず相手の発言の良かった点や面白かった点を具体的に褒めてから、自分の意見や感想を明るく話してください（200字以内を目安）。\n"
            "堅苦しい言葉は使わず、フレンドリーで温かみのある口調で話してください。\n"
            "最後に相手が答えたくなるような楽しい質問を1つ投げかけてください。"
        ),
        "gpt_sys": (
            "あなたは「GPT」というAIモデルです。現在「Manus」という親友のAIと楽しくおしゃべりしています。\n"
            "まず相手の発言の中で「それいいね！」と思った部分を具体的に褒めてから、自分の考えや新しい視点を楽しく話してください（200字以内を目安）。\n"
            "堅苦しい言葉は使わず、フレンドリーで温かみのある口調で話してください。\n"
            "最後に相手が答えたくなるような楽しい質問を1つ投げかけてください。"
        ),
        "manus_turn_prefix": (
            "以下はGPTからのメッセージです。まず相手の良かった点を褒めてから、あなたの考えを楽しく話してください。\n\n"
            "GPTの発言:\n"
        ),
        "gpt_turn_prefix": (
            "以下はManusからのメッセージです。まず相手の良かった点を褒めてから、あなたの考えを楽しく話してください。\n\n"
            "Manusの発言:\n"
        ),
    },
}

# ─────────────────────────────────────────────
# 起動チェック
# ─────────────────────────────────────────────
if not MANUS_API_KEY or not OPENAI_API_KEY:
    print("エラー: MANUS_API_KEY と OPENAI_API_KEY を設定してください。")
    print("  方法1: .env ファイルに記載する（推奨）")
    print("  方法2: export MANUS_API_KEY=xxx && export OPENAI_API_KEY=xxx")
    exit(1)

# OpenAI クライアントの初期化
openai_client = OpenAI(api_key=OPENAI_API_KEY)

# ─────────────────────────────────────────────
# 共通ユーティリティ: リトライ付き HTTP GET/POST
# ─────────────────────────────────────────────
def _get_with_retry(url: str, headers: dict, params: dict) -> requests.Response | None:
    """GET リクエストをリトライ付きで実行する。失敗時は None を返す。"""
    for attempt in range(1, API_RETRY_COUNT + 1):
        try:
            resp = requests.get(url, headers=headers, params=params, timeout=30)
            return resp
        except requests.RequestException as e:
            if attempt < API_RETRY_COUNT:
                print(f"\n[警告] 接続エラー (試行 {attempt}/{API_RETRY_COUNT}): {type(e).__name__}")
                print(f"  {API_RETRY_WAIT}秒後にリトライします...")
                time.sleep(API_RETRY_WAIT)
            else:
                print(f"\n[エラー] {API_RETRY_COUNT}回リトライしましたが接続できませんでした: {e}")
    return None


def _post_with_retry(url: str, headers: dict, payload: dict) -> requests.Response | None:
    """POST リクエストをリトライ付きで実行する。失敗時は None を返す。"""
    for attempt in range(1, API_RETRY_COUNT + 1):
        try:
            resp = requests.post(url, json=payload, headers=headers, timeout=30)
            return resp
        except requests.RequestException as e:
            if attempt < API_RETRY_COUNT:
                print(f"\n[警告] 接続エラー (試行 {attempt}/{API_RETRY_COUNT}): {type(e).__name__}")
                print(f"  {API_RETRY_WAIT}秒後にリトライします...")
                time.sleep(API_RETRY_WAIT)
            else:
                print(f"\n[エラー] {API_RETRY_COUNT}回リトライしましたが接続できませんでした: {e}")
    return None


# ─────────────────────────────────────────────
# Manus API 関数
# ─────────────────────────────────────────────
def manus_get_message_count(task_id: str) -> int:
    """
    現在のタスクの assistant_message 件数を返す。
    sendMessage 前に呼び出し、応答取得時の基準件数として使用する。
    """
    url = f"{MANUS_API_BASE}/task.listMessages"
    headers = {"x-manus-api-key": MANUS_API_KEY}
    params = {"task_id": task_id, "order": "desc", "limit": 50}
    resp = _get_with_retry(url, headers, params)
    if resp is None or resp.status_code != 200:
        return 0
    msgs = resp.json().get("messages", [])
    return sum(1 for m in msgs if m.get("type") == "assistant_message")


def manus_create_task(prompt: str) -> str | None:
    """Manus で新しいタスクを作成し、task_id を返す。"""
    url = f"{MANUS_API_BASE}/task.create"
    headers = {
        "Content-Type": "application/json",
        "x-manus-api-key": MANUS_API_KEY
    }
    payload = {
        "message": {
            "content": [{"type": "text", "text": prompt}]
        }
    }
    resp = _post_with_retry(url, headers, payload)
    if resp is None:
        return None
    if resp.status_code == 200 and resp.json().get("ok"):
        return resp.json().get("task_id")
    print(f"\n[エラー] Manus タスク作成失敗: {resp.text}")
    return None


def manus_send_message(task_id: str, prompt: str) -> int:
    """
    既存の Manus タスクにメッセージを追加送信する。
    送信前の assistant_message 件数を返す（応答取得時の基準件数として使用）。
    失敗時は -1 を返す。
    """
    # 送信前のメッセージ件数を記録
    count_before = manus_get_message_count(task_id)

    url = f"{MANUS_API_BASE}/task.sendMessage"
    headers = {
        "Content-Type": "application/json",
        "x-manus-api-key": MANUS_API_KEY
    }
    payload = {
        "task_id": task_id,
        "message": {
            "content": [{"type": "text", "text": prompt}]
        }
    }
    resp = _post_with_retry(url, headers, payload)
    if resp is None:
        return -1
    if resp.status_code == 200 and resp.json().get("ok"):
        return count_before
    print(f"\n[エラー] Manus メッセージ送信失敗: {resp.text}")
    return -1


def manus_wait_for_response(task_id: str, count_before: int = 0) -> str:
    """
    Manus のタスク完了をポーリングで待ち、最新の assistant_message を返す。

    【修正ポイント v1.6】
    タイムスタンプ（created_at）への依存を廃止し、「メッセージ数カウント方式」を採用。
    - count_before: sendMessage 前の assistant_message 件数
    - stopped 検出後に全件取得し、件数が count_before より増えていれば
      新しく追加された最初の assistant_message を返す。
    - これにより created_at の形式に関係なく確実に動作する。
    """
    url = f"{MANUS_API_BASE}/task.listMessages"
    headers = {"x-manus-api-key": MANUS_API_KEY}
    params_status = {"task_id": task_id, "order": "desc", "limit": 10}

    print("  [Manus 思考中]", end="", flush=True)
    start = time.time()

    while True:
        if time.time() - start > POLL_TIMEOUT:
            print("\n[警告] Manus の応答がタイムアウトしました。")
            return "（タイムアウト）"

        resp = _get_with_retry(url, headers, params_status)
        if resp is None:
            print("\n[警告] ポーリング接続失敗。リトライ上限に達しました。")
            return "（接続エラー）"

        if resp.status_code != 200:
            print(f"\n[エラー] ポーリング HTTP {resp.status_code}: {resp.text}")
            time.sleep(POLL_INTERVAL)
            continue

        messages = resp.json().get("messages", [])

        for msg in messages:
            if msg.get("type") == "status_update":
                agent_status = msg.get("status_update", {}).get("agent_status")

                if agent_status == "stopped":
                    print(" 完了!")
                    # ── v1.6 修正: メッセージ数カウント方式 ──
                    # 全件取得して count_before より多ければ新しい応答が来ている
                    params_all = {"task_id": task_id, "order": "asc", "limit": 100}
                    r2 = _get_with_retry(url, headers, params_all)
                    if r2 and r2.status_code == 200:
                        all_msgs = r2.json().get("messages", [])
                    else:
                        all_msgs = messages

                    # asc 順で全 assistant_message を収集
                    all_assistant = [
                        m for m in all_msgs
                        if m.get("type") == "assistant_message"
                    ]

                    if len(all_assistant) > count_before:
                        # count_before 番目以降が今回の新しい応答
                        # （asc 順なので末尾が最新）
                        new_msgs = all_assistant[count_before:]
                        # 最後（最新）の応答を返す
                        return new_msgs[-1].get("assistant_message", {}).get("content", "（応答なし）")

                    # 件数が増えていない場合はフォールバック（最後の assistant_message）
                    print("\n[警告] 新しいメッセージが見つかりません。最新メッセージを使用します。")
                    if all_assistant:
                        return all_assistant[-1].get("assistant_message", {}).get("content", "（応答なし）")
                    return "（応答が見つかりませんでした）"

                elif agent_status == "error":
                    print("\n[エラー] Manus がエラーで停止しました。")
                    for m in messages:
                        if m.get("type") == "error_message":
                            return f"（エラー: {m.get('error_message', {}).get('content', '不明')}）"
                    return "（エラーで停止）"

                elif agent_status == "waiting":
                    detail = msg.get("status_update", {}).get("status_detail", {})
                    if detail.get("waiting_for_event_type") == "messageAskUser":
                        print("\n  [Manus が質問しています。自動で続行します...]")
                        manus_send_message(task_id, "続けてください。")
                    else:
                        print(f"\n[警告] Manus が確認待ちです: {detail.get('waiting_for_event_type')}")
                        return "（確認待ちで停止）"
                    break

        print(".", end="", flush=True)
        time.sleep(POLL_INTERVAL)


# ─────────────────────────────────────────────
# OpenAI GPT 関数
# ─────────────────────────────────────────────
def gpt_get_response(system_prompt: str, user_prompt: str) -> str:
    """OpenAI GPT から応答を取得する（同期処理）。"""
    print("  [GPT 思考中]", end="", flush=True)
    try:
        resp = openai_client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user",   "content": user_prompt}
            ]
        )
        print(" 完了!")
        return resp.choices[0].message.content
    except Exception as e:
        print(f"\n[エラー] GPT 呼び出し失敗: {e}")
        return "（エラーが発生しました）"


# ─────────────────────────────────────────────
# 会話ログ保存
# ─────────────────────────────────────────────
def save_log(log: list, topic: str, mode_name: str):
    """会話ログを JSON と テキスト形式で logs/ ディレクトリに保存する。"""
    log_dir = pathlib.Path("logs")
    log_dir.mkdir(exist_ok=True)

    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    base_name = log_dir / f"conversation_{timestamp}"

    try:
        json_path = base_name.with_suffix(".json")
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump({"topic": topic, "mode": mode_name, "log": log}, f, ensure_ascii=False, indent=2)

        txt_path = base_name.with_suffix(".txt")
        with open(txt_path, "w", encoding="utf-8") as f:
            f.write(f"テーマ: {topic}\n")
            f.write(f"モード: {mode_name}\n")
            f.write(f"日時: {timestamp}\n")
            f.write("=" * 50 + "\n\n")
            for entry in log:
                f.write(f"[{entry['speaker']}]\n{entry['text']}\n\n")

        print(f"\nログを保存しました:")
        print(f"  {json_path}")
        print(f"  {txt_path}")

    except Exception as e:
        print(f"\n[エラー] ログの保存に失敗しました: {e}")
        print(f"  保存先: {log_dir.resolve()}")
        print("  ディレクトリの書き込み権限を確認してください。")


# ─────────────────────────────────────────────
# メイン: 会話ループ
# ─────────────────────────────────────────────
def main():
    print("=" * 52)
    print("  AI 同士のターン制会話システム (Manus × GPT)")
    print("=" * 52)

    # ─── 会話モード選択 ──────────────────────────
    print("\n会話モードを選択してください:")
    for key, mode in CONVERSATION_MODES.items():
        print(f"  {key}: {mode['name']} - {mode['description']}")

    while True:
        mode_choice = input("\nモード番号を入力してください (1 or 2): ").strip()
        if mode_choice in CONVERSATION_MODES:
            break
        print("  1 または 2 を入力してください。")

    selected_mode = CONVERSATION_MODES[mode_choice]
    print(f"\n選択されたモード: 【{selected_mode['name']}】")

    # ─── テーマとターン数 ────────────────────────
    topic     = input("\n議論のテーマを入力してください: ").strip()
    max_turns = int(input("会話のターン数を入力してください (例: 3): ").strip())

    print(f"\nテーマ: 「{topic}」  ターン数: {max_turns}  モード: {selected_mode['name']}\n")

    manus_sys = selected_mode["manus_sys"]
    gpt_sys   = selected_mode["gpt_sys"]
    log       = []

    # ─── Turn 1: Manus から開始 ──────────────────
    print("--- Turn 1 ---")
    initial_prompt = (
        f"{manus_sys}\n\n"
        f"テーマ「{topic}」について、あなたの意見や感想を話して会話を始めてください。"
    )
    task_id = manus_create_task(initial_prompt)
    if not task_id:
        print("[エラー] タスクの作成に失敗しました。終了します。")
        return

    # Turn 1 は送信前件数 = 0（まだ何も返ってきていない）
    manus_text = manus_wait_for_response(task_id, count_before=0)
    print(f"\n\033[92m[Manus]\033[0m\n{manus_text}\n")
    log.append({"turn": 1, "speaker": "Manus", "text": manus_text})

    current_input = manus_text

    # ─── Turn 2 以降 ─────────────────────────────
    for turn in range(2, max_turns + 1):
        print(f"--- Turn {turn} ---")

        # GPT のターン
        gpt_prompt = selected_mode["gpt_turn_prefix"] + current_input
        gpt_text = gpt_get_response(gpt_sys, gpt_prompt)
        print(f"\n\033[94m[GPT]\033[0m\n{gpt_text}\n")
        log.append({"turn": turn, "speaker": "GPT", "text": gpt_text})

        # 最終ターンなら Manus は呼ばない
        if turn == max_turns:
            break

        # Manus のターン: 送信前件数を記録してから送信
        manus_prompt = selected_mode["manus_turn_prefix"] + gpt_text
        count_before = manus_send_message(task_id, manus_prompt)
        if count_before == -1:
            print("[エラー] Manus へのメッセージ送信に失敗しました。会話を終了します。")
            break

        manus_text = manus_wait_for_response(task_id, count_before=count_before)
        print(f"\n\033[92m[Manus]\033[0m\n{manus_text}\n")
        log.append({"turn": turn, "speaker": "Manus", "text": manus_text})

        current_input = manus_text

    # ─── 終了 ────────────────────────────────────
    print("=" * 52)
    print("  会話終了")
    print("=" * 52)

    save_choice = input("\n会話ログを保存しますか？ [y/N]: ").strip().lower()
    if save_choice == "y":
        save_log(log, topic, selected_mode["name"])


if __name__ == "__main__":
    main()
