"""
conversation_engine.py  v2 (マルチAI対応版)
============================================
Manus / OpenAI(GPT) / Claude の中から2〜3体を選び、司会役(モデレーター)を1体指定して、
テーマについて話し合わせるエンジン。app.py から呼び出される。

v1(2AI固定・Manus特別扱い)からの主な変更点:
  - Claude(Anthropic API)を追加。
  - 会話履歴をPython側の `log` リストで一元管理し、毎ターン「これまでの会話」を
    テキストに整形して**全参加AIに** system_prompt + transcript として渡す設計に変更した。
    これにより、Manusのtask機能(サーバー側で会話を覚える)に頼る必要がなくなった。
  - Manusは「1ターン=新規タスク作成」に変更(旧: 同じtask_idにsendMessageし続ける方式)。
    これに伴い、旧v1にあった「count_before(メッセージ数カウント)」の仕組みは丸ごと不要になり、
    Manus固有の複雑さを削減した。
  - 「モデレーター」(司会役)と「参加者」でsystem_promptを分け、モデレーターが
    Turn1で会話を開始し、以降は「モデレーター以外→...→モデレーター」の順で
    ローテーションする(モデレーターの連続発言を避けるため)。
  - OpenAI/Claudeは呼び出し時にモデル名を指定できる。Manusは、Manus API v2の
    task.create にモデル選択パラメータが存在するか現時点で確認できていないため、
    モデル選択には対応していない(TODO: 公式ドキュメントで要確認)。

Manus/OpenAI/ClaudeへのAPI呼び出しの土台(リトライ処理)は v1 から変更していない。
"""

import os
import re
import time
import json
import datetime
import pathlib
import threading
import requests
from openai import OpenAI
import anthropic
from dotenv import load_dotenv

load_dotenv()

# ─────────────────────────────────────────────
# 設定
# ─────────────────────────────────────────────
MANUS_API_KEY     = os.environ.get("MANUS_API_KEY")
OPENAI_API_KEY    = os.environ.get("OPENAI_API_KEY")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")

MANUS_API_BASE   = "https://api.manus.ai/v2"
POLL_INTERVAL    = 5
POLL_TIMEOUT     = 300
API_RETRY_COUNT  = 3
API_RETRY_WAIT   = 10
CLAUDE_MAX_TOKENS = 1024

openai_client = OpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None
claude_client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY) if ANTHROPIC_API_KEY else None

# ─────────────────────────────────────────────
# AIプロバイダ定義
# ─────────────────────────────────────────────
PROVIDER_DISPLAY_NAME = {"manus": "Manus", "openai": "GPT", "claude": "Claude"}
CANONICAL_ORDER = ["manus", "openai", "claude"]

# UIに表示するモデル候補(いずれも「custom」でユーザーが自由入力した値を優先する)。
# OpenAI/Claudeともに新しいモデルが随時追加されるため、ここに無いモデル名を
# 使いたい場合はUI側の「カスタム」欄に直接入力できるようにしてある。
MODEL_CHOICES = {
    "openai": ["gpt-4o-mini", "gpt-4o"],
    "claude": ["claude-sonnet-5", "claude-opus-5", "claude-haiku-4-5-20251001"],
    # manus: モデル選択非対応(上記の理由により)
}

TONE_PRESETS = {
    "1": {
        "name": "真面目モード",
        "style": "論理的かつ簡潔に、建設的な議論を行ってください。相手の意見に同意するだけでなく、必要であれば異なる視点も提示してください。",
    },
    "2": {
        "name": "フレンドリーモード",
        "style": "フレンドリーで温かみのある口調で話してください。他の参加者の発言の良かった点を具体的に褒めてから、自分の意見を述べてください。",
    },
}


class ConversationControl:
    """実行中の会話1件に対する PAUSE / RESUME / STOP 操作を仲介する。"""

    def __init__(self):
        self._pause = threading.Event()
        self._stop = threading.Event()

    def pause(self):
        self._pause.set()

    def resume(self):
        self._pause.clear()

    def stop(self):
        self._stop.set()
        self._pause.clear()

    def is_stopped(self) -> bool:
        return self._stop.is_set()

    def wait_if_paused(self):
        while self._pause.is_set() and not self._stop.is_set():
            time.sleep(0.2)


# ─────────────────────────────────────────────
# 共通ユーティリティ: リトライ付き HTTP GET/POST
# ─────────────────────────────────────────────
def _get_with_retry(url: str, headers: dict, params: dict):
    for attempt in range(1, API_RETRY_COUNT + 1):
        try:
            return requests.get(url, headers=headers, params=params, timeout=30)
        except requests.RequestException as e:
            if attempt < API_RETRY_COUNT:
                print(f"[警告] 接続エラー (試行 {attempt}/{API_RETRY_COUNT}): {type(e).__name__}")
                time.sleep(API_RETRY_WAIT)
            else:
                print(f"[エラー] {API_RETRY_COUNT}回リトライしましたが接続できませんでした: {e}")
    return None


def _post_with_retry(url: str, headers: dict, payload: dict):
    for attempt in range(1, API_RETRY_COUNT + 1):
        try:
            return requests.post(url, json=payload, headers=headers, timeout=30)
        except requests.RequestException as e:
            if attempt < API_RETRY_COUNT:
                print(f"[警告] 接続エラー (試行 {attempt}/{API_RETRY_COUNT}): {type(e).__name__}")
                time.sleep(API_RETRY_WAIT)
            else:
                print(f"[エラー] {API_RETRY_COUNT}回リトライしましたが接続できませんでした: {e}")
    return None


# ─────────────────────────────────────────────
# Manus API関数(v2: 1ターン=新規タスク作成に変更)
# ─────────────────────────────────────────────
def manus_create_task(prompt: str):
    url = f"{MANUS_API_BASE}/task.create"
    headers = {"Content-Type": "application/json", "x-manus-api-key": MANUS_API_KEY}
    payload = {"message": {"content": [{"type": "text", "text": prompt}]}}
    resp = _post_with_retry(url, headers, payload)
    if resp is None:
        return None
    if resp.status_code == 200 and resp.json().get("ok"):
        return resp.json().get("task_id")
    print(f"[エラー] Manus タスク作成失敗: {resp.text}")
    return None


def manus_send_message(task_id: str, prompt: str) -> bool:
    """既存タスクへの追加送信(messageAskUserへの自動応答用)。成否のみ返す。"""
    url = f"{MANUS_API_BASE}/task.sendMessage"
    headers = {"Content-Type": "application/json", "x-manus-api-key": MANUS_API_KEY}
    payload = {"task_id": task_id, "message": {"content": [{"type": "text", "text": prompt}]}}
    resp = _post_with_retry(url, headers, payload)
    if resp is not None and resp.status_code == 200 and resp.json().get("ok"):
        return True
    print(f"[エラー] Manus メッセージ送信失敗: {resp.text if resp is not None else '(接続エラー)'}")
    return False


def manus_wait_for_response(task_id: str, control: ConversationControl) -> str:
    """
    新規タスクの完了をポーリングで待ち、assistant_messageを返す。
    タスクは1ターンごとに新規作成するため、v1にあった「count_before」による
    メッセージ数カウントは不要(タスク内の最後のassistant_messageを使えばよい)。
    """
    url = f"{MANUS_API_BASE}/task.listMessages"
    headers = {"x-manus-api-key": MANUS_API_KEY}
    params = {"task_id": task_id, "order": "desc", "limit": 10}

    start = time.time()
    while True:
        if control.is_stopped():
            return "(ユーザー操作により停止されました)"

        if time.time() - start > POLL_TIMEOUT:
            print("[警告] Manus の応答がタイムアウトしました。")
            return "(タイムアウト)"

        resp = _get_with_retry(url, headers, params)
        if resp is None:
            return "(接続エラー)"
        if resp.status_code != 200:
            time.sleep(POLL_INTERVAL)
            continue

        messages = resp.json().get("messages", [])
        for msg in messages:
            if msg.get("type") == "status_update":
                agent_status = msg.get("status_update", {}).get("agent_status")

                if agent_status == "stopped":
                    params_all = {"task_id": task_id, "order": "asc", "limit": 100}
                    r2 = _get_with_retry(url, headers, params_all)
                    all_msgs = r2.json().get("messages", []) if (r2 and r2.status_code == 200) else messages
                    assistant_msgs = [m for m in all_msgs if m.get("type") == "assistant_message"]
                    if assistant_msgs:
                        return assistant_msgs[-1].get("assistant_message", {}).get("content", "(応答なし)")
                    return "(応答が見つかりませんでした)"

                elif agent_status == "error":
                    for m in messages:
                        if m.get("type") == "error_message":
                            return f"(エラー: {m.get('error_message', {}).get('content', '不明')})"
                    return "(エラーで停止)"

                elif agent_status == "waiting":
                    detail = msg.get("status_update", {}).get("status_detail", {})
                    if detail.get("waiting_for_event_type") == "messageAskUser":
                        print("[Manus が質問しています。自動で続行します...]")
                        manus_send_message(task_id, "続けてください。")
                    else:
                        return "(確認待ちで停止)"
                    break

        time.sleep(POLL_INTERVAL)


# ─────────────────────────────────────────────
# OpenAI / Claude 呼び出し
# ─────────────────────────────────────────────
def _call_openai(model: str, system_prompt: str, user_prompt: str, json_mode: bool = False) -> str:
    if openai_client is None:
        return "(OPENAI_API_KEYが設定されていません)"
    try:
        kwargs = {}
        if json_mode:
            # OpenAIのJSONモード。system/userのいずれかに"JSON"という語が
            # 含まれていないとAPI側がエラーを返す仕様のため、呼び出し元のプロンプトで
            # 必ず「JSON」という語を含めること(generate_summary側で対応済み)。
            kwargs["response_format"] = {"type": "json_object"}
        resp = openai_client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            **kwargs,
        )
        return resp.choices[0].message.content
    except Exception as e:
        print(f"[エラー] OpenAI 呼び出し失敗: {e}")
        return "(エラーが発生しました)"


def _call_claude(model: str, system_prompt: str, user_prompt: str) -> str:
    if claude_client is None:
        return "(ANTHROPIC_API_KEYが設定されていません)"
    try:
        resp = claude_client.messages.create(
            model=model,
            max_tokens=CLAUDE_MAX_TOKENS,
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
        )
        return resp.content[0].text
    except Exception as e:
        print(f"[エラー] Claude 呼び出し失敗: {e}")
        return "(エラーが発生しました)"


def call_ai(provider: str, model: str, system_prompt: str, user_prompt: str,
            control: ConversationControl, json_mode: bool = False) -> str:
    if provider == "openai":
        return _call_openai(model, system_prompt, user_prompt, json_mode=json_mode)
    if provider == "claude":
        return _call_claude(model, system_prompt, user_prompt)
    if provider == "manus":
        full_prompt = f"{system_prompt}\n\n{user_prompt}"
        task_id = manus_create_task(full_prompt)
        if not task_id:
            return "(Manusタスクの作成に失敗しました)"
        return manus_wait_for_response(task_id, control)
    raise ValueError(f"unknown provider: {provider}")


# ─────────────────────────────────────────────
# system_prompt / transcript 組み立て
# ─────────────────────────────────────────────
def build_system_prompt(provider: str, is_moderator: bool, participant_names: list,
                         moderator_name: str, tone_key: str) -> str:
    name = PROVIDER_DISPLAY_NAME[provider]
    tone = TONE_PRESETS[tone_key]["style"]
    others = "、".join(n for n in participant_names if n != name)

    if is_moderator:
        return (
            f"あなたは「{name}」というAIで、この会話の司会・進行役です。参加者: {others}。\n"
            f"テーマに沿って議論が深まるよう、質問を投げかけたり、話を整理したり、"
            f"参加者の発言を踏まえて次の話題を提示したりしてください。{tone}\n"
            f"発言は200字程度を目安にしてください。"
        )
    return (
        f"あなたは「{name}」というAIです。司会役の「{moderator_name}」が進行するディスカッションに"
        f"参加者として加わっています。他の参加者: {others}。\n"
        f"これまでの会話を踏まえて、自分の意見や感想を述べたり、他の参加者の発言に"
        f"反応したりしてください。{tone}\n"
        f"発言は200字程度を目安にしてください。"
    )


def build_transcript(log: list) -> str:
    if not log:
        return "(まだ発言はありません)"
    return "\n\n".join(f"[Turn {e['turn']}] {e['speaker']}: {e['text']}" for e in log)


# ─────────────────────────────────────────────
# 会話終了後の要約生成
# ─────────────────────────────────────────────
# 要約担当AIの優先順位。JSON出力の安定性が高い順(OpenAIはJSONモードあり、
# Claudeはプロンプト指示への追従性が高い、Manusはエージェント的な応答で
# JSON以外の文章が混ざりやすいため最後)。実際に参加しているAIの中から選ぶ。
SUMMARY_PROVIDER_PREFERENCE = ["openai", "claude", "manus"]

SUMMARY_JSON_SCHEMA_HINT = (
    '{"overall_summary": "会話全体の総括(2〜3文)", '
    '"per_speaker": [{"speaker": "話者名", "points": ["視点や主張を簡潔に", "..."]}], '
    '"key_issues": ["会話全体を通じて浮かび上がった論点・課題", "..."], '
    '"divergent_points": [{"point": "対立点の見出し", "description": "どの話者がどう異なる立場を取ったかを1〜2文で"}]}'
)


def pick_summarizer(participants: list) -> dict:
    """参加AIの中から、要約生成に使うAIをJSON出力の安定性が高い順に選ぶ。"""
    by_provider = {p["provider"]: p for p in participants}
    for pref in SUMMARY_PROVIDER_PREFERENCE:
        if pref in by_provider:
            return by_provider[pref]
    return participants[0]


def _extract_json(text: str):
    """LLMの応答からJSON部分を取り出す。```json ...``` のコードフェンスや、
    前後に説明文が付いている場合にも対応する。失敗した場合はNoneを返す。"""
    if not text:
        return None
    text = text.strip()

    fence_match = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.DOTALL)
    candidate = fence_match.group(1) if fence_match else text

    try:
        return json.loads(candidate)
    except (json.JSONDecodeError, TypeError):
        pass

    start = candidate.find("{")
    end = candidate.rfind("}")
    if start != -1 and end != -1 and end > start:
        try:
            return json.loads(candidate[start:end + 1])
        except json.JSONDecodeError:
            return None
    return None


def generate_summary(topic: str, participant_names: list, log: list, participants: list,
                      control: ConversationControl) -> dict:
    """
    会話ログ全体から、各AIの視点・論点・AI間で見解が分かれた点をビジネス向けに整理した
    要約を生成する。戻り値は必ず overall_summary / per_speaker / key_issues /
    divergent_points / raw_text のキーを持つ。LLMの応答がJSONとして解釈できなかった場合は、
    raw_text に生テキストを入れてフロントエンド側でそのまま表示できるようにする
    (構造化には失敗しても、要約そのものは失われないようにするフォールバック設計)。
    """
    summarizer = pick_summarizer(participants)
    provider = summarizer["provider"]
    model = summarizer.get("model")

    system_prompt = (
        "あなたはビジネス向けの会議サマリー作成者です。複数の生成AIによる会話ログを分析し、"
        "経営層が読むことを想定した、簡潔で具体的な要約を作成してください。"
        "出力は必ず次のJSON形式のみとし、前後に説明文やコードフェンスを含めないでください。\n"
        f"{SUMMARY_JSON_SCHEMA_HINT}"
    )
    user_prompt = (
        f"テーマ: 「{topic}」\n参加AI: {', '.join(participant_names)}\n\n"
        f"会話ログ:\n{build_transcript(log)}\n\n"
        "上記の会話ログを分析し、指定のJSON形式で出力してください。"
        "per_speakerには実際に発言した話者ごとに、その話者が述べた視点・主張・懸念点を"
        "3〜5項目程度の簡潔な箇条書きでまとめてください。"
        "divergent_pointsには、参加AIの間で意見・立場が明確に分かれた論点だけを抽出してください"
        "(単なる言い回しの違いではなく、実質的に異なる主張・評価をしている箇所を選ぶこと)。"
        "見解の相違が実質的に無かった場合は、divergent_pointsは空配列にしてください。"
    )

    raw = call_ai(provider, model, system_prompt, user_prompt, control, json_mode=(provider == "openai"))
    parsed = _extract_json(raw)

    if isinstance(parsed, dict) and "per_speaker" in parsed:
        return {
            "overall_summary": parsed.get("overall_summary", ""),
            "per_speaker": parsed.get("per_speaker", []),
            "key_issues": parsed.get("key_issues", []),
            "divergent_points": parsed.get("divergent_points", []),
            "raw_text": None,
        }

    # JSONとして解釈できなかった場合のフォールバック(生成AIの生テキストをそのまま出す)
    return {
        "overall_summary": "",
        "per_speaker": [],
        "key_issues": [],
        "divergent_points": [],
        "raw_text": raw,
    }


# ─────────────────────────────────────────────
# ログ保存
# ─────────────────────────────────────────────
def save_log(log: list, topic: str, tone_name: str, participant_names: list,
             moderator_name: str, summary: dict | None = None):
    """会話ログ(と要約があればそれも含めて)を .json / .txt の両方で保存する。
    戻り値は {"json": ファイル名, "txt": ファイル名} 。失敗時はNoneを返す。"""
    log_dir = pathlib.Path("logs")
    log_dir.mkdir(exist_ok=True)
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    base_name = log_dir / f"conversation_{timestamp}"
    try:
        json_path = base_name.with_suffix(".json")
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump({
                "topic": topic,
                "tone": tone_name,
                "participants": participant_names,
                "moderator": moderator_name,
                "log": log,
                "summary": summary,
            }, f, ensure_ascii=False, indent=2)

        txt_path = base_name.with_suffix(".txt")
        with open(txt_path, "w", encoding="utf-8") as f:
            f.write(f"テーマ: {topic}\nトーン: {tone_name}\n参加AI: {', '.join(participant_names)}\n")
            f.write(f"モデレーター: {moderator_name}\n日時: {timestamp}\n" + "=" * 50 + "\n\n")
            for entry in log:
                f.write(f"[{entry['speaker']}]\n{entry['text']}\n\n")

            if summary:
                f.write("=" * 50 + "\n会話の要約\n" + "=" * 50 + "\n\n")
                if summary.get("overall_summary"):
                    f.write(summary["overall_summary"] + "\n\n")
                for item in summary.get("per_speaker", []):
                    f.write(f"[{item.get('speaker', '')}の視点]\n")
                    for pt in item.get("points", []):
                        f.write(f"  - {pt}\n")
                    f.write("\n")
                if summary.get("key_issues"):
                    f.write("主な論点・課題:\n")
                    for k in summary["key_issues"]:
                        f.write(f"  - {k}\n")
                    f.write("\n")
                if summary.get("divergent_points"):
                    f.write("AI間で見解が分かれた点:\n")
                    for d in summary["divergent_points"]:
                        f.write(f"  - {d.get('point', '')}: {d.get('description', '')}\n")
                    f.write("\n")
                if summary.get("raw_text"):
                    f.write(summary["raw_text"] + "\n")

        print(f"ログを保存しました: {json_path} / {txt_path}")
        return {"json": json_path.name, "txt": txt_path.name}
    except Exception as e:
        print(f"[エラー] ログの保存に失敗しました: {e}")
        return None


# ─────────────────────────────────────────────
# 会話ループ本体(app.pyがバックグラウンドスレッドで起動する)
# ─────────────────────────────────────────────
def run_conversation(topic: str, tone_key: str, max_turns: int, participants: list,
                      moderator_provider: str, control: ConversationControl, broadcaster):
    """
    participants: [{"provider": "manus"|"openai"|"claude", "model": str|None}, ...] (2〜3件)
    moderator_provider: participants に含まれる provider のいずれか

    Turn1はモデレーターの開始発言。Turn2以降は「モデレーター以外 → ... → モデレーター」の
    順でローテーションする(モデレーターの連続発言を避けるため)。
    """

    def emit(event: dict):
        print(f"[event] {event}")
        broadcaster.publish(event)

    provider_set = {p["provider"] for p in participants}

    missing = []
    if "manus" in provider_set and not MANUS_API_KEY:
        missing.append("MANUS_API_KEY")
    if "openai" in provider_set and not OPENAI_API_KEY:
        missing.append("OPENAI_API_KEY")
    if "claude" in provider_set and not ANTHROPIC_API_KEY:
        missing.append("ANTHROPIC_API_KEY")
    if missing:
        emit({"type": "error", "source": "config",
              "message": f"{', '.join(missing)} が設定されていません。.envを確認してください。"})
        emit({"type": "session_end", "result": "failed"})
        return

    participant_names = [PROVIDER_DISPLAY_NAME[p["provider"]] for p in participants]
    moderator_name = PROVIDER_DISPLAY_NAME[moderator_provider]
    tone_name = TONE_PRESETS[tone_key]["name"]

    ordered = [p for prov in CANONICAL_ORDER for p in participants if p["provider"] == prov]
    mod_index = next(i for i, p in enumerate(ordered) if p["provider"] == moderator_provider)
    # モデレーターの直後から始まり、モデレーターで終わる順(モデレーターの連続発言を避ける)
    rotation = ordered[mod_index + 1:] + ordered[:mod_index + 1]

    emit({
        "type": "session_start",
        "topic": topic,
        "tone": tone_key,
        "tone_name": tone_name,
        "max_turns": max_turns,
        "participants": participant_names,
        "moderator": moderator_name,
    })

    log = []

    def speak(turn_no: int, participant: dict, is_moderator: bool, opening: bool):
        provider = participant["provider"]
        name = PROVIDER_DISPLAY_NAME[provider]
        emit({"type": "status", "agent": name, "state": "thinking"})

        system_prompt = build_system_prompt(provider, is_moderator, participant_names, moderator_name, tone_key)
        if opening:
            user_prompt = f"テーマ「{topic}」について、司会として会話を始めてください。"
        else:
            transcript = build_transcript(log)
            user_prompt = f"これまでの会話:\n{transcript}\n\nあなたの番です。次の発言をしてください。"

        text = call_ai(provider, participant.get("model"), system_prompt, user_prompt, control)

        log.append({"turn": turn_no, "speaker": name, "text": text})
        emit({"type": "message", "id": f"{provider}-{turn_no}", "turn": turn_no, "speaker": name, "text": text})

    def finish(result: str):
        summary = None
        if len(log) >= 2:
            emit({"type": "status", "agent": "要約", "state": "generating"})
            summary = generate_summary(topic, participant_names, log, participants, control)
            emit({"type": "summary", **summary})

        saved = save_log(log, topic, tone_name, participant_names, moderator_name, summary)
        emit({
            "type": "session_end",
            "result": result,
            "log_file_json": saved["json"] if saved else None,
            "log_file_txt": saved["txt"] if saved else None,
        })

    # ─── Turn 1: モデレーターの開始発言 ───
    moderator_participant = next(p for p in participants if p["provider"] == moderator_provider)
    speak(1, moderator_participant, is_moderator=True, opening=True)

    if control.is_stopped():
        finish("stopped")
        return

    # ─── Turn 2以降: ローテーション ───
    turn_no = 2
    rotation_idx = 0
    while turn_no <= max_turns:
        control.wait_if_paused()
        if control.is_stopped():
            finish("stopped")
            return

        participant = rotation[rotation_idx % len(rotation)]
        is_mod = participant["provider"] == moderator_provider
        speak(turn_no, participant, is_moderator=is_mod, opening=False)

        if control.is_stopped():
            finish("stopped")
            return

        rotation_idx += 1
        turn_no += 1

    finish("completed")
