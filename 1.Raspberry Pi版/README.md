# Raspberry Pi版 ── AI会話サーバー(Web UI + CrowPanel向けAPI)

Manus / GPT(OpenAI) / Claude(Anthropic) / GLM(z.AI) の中から2〜5体を選んで会話させる、
Flask製のサーバーです。ブラウザから操作できるほか、[CrowPanel(`../2.ESP32版/`)](../2.ESP32版/)
からも同じ操作・表示ができるよう、ポーリング用のAPIを備えています。

- 会話の進行は、ブラウザには SSE(`/events`)、CrowPanelにはポーリング(`/api/events`)で配信します。
- **APIキーはこのサーバー上の `.env` にだけ置きます。** ブラウザにもCrowPanelにも送りません。

## 必要なもの

| 項目 | 内容 |
| :--- | :--- |
| ハードウェア | Raspberry Pi(Zero 2 W / 3B+ / 4 / 5 のいずれも可)、電源、microSDカード |
| OS | Raspberry Pi OS 64bit(Bookworm以降) |
| Python | **3.10以上**(Bookwormの標準は3.11) |
| ネットワーク | インターネット接続、PCやCrowPanelと同じLAN |
| APIキー | 参加させたいAIの分だけでOK(下記) |

| AI | キー(`.env`の項目) | 取得先 |
| :--- | :--- | :--- |
| Manus | `MANUS_API_KEY` | [Manusダッシュボード](https://manus.im/app?show_settings=integrations&app_name=api) |
| GPT (OpenAI) | `OPENAI_API_KEY` | [OpenAI Platform](https://platform.openai.com/api-keys) |
| Claude (Anthropic) | `ANTHROPIC_API_KEY` | [Anthropic Console](https://console.anthropic.com/settings/keys) |
| GLM (z.AI) | `ZAI_API_KEY` | [z.AI](https://z.ai/manage-apikey/apikey-list) |

> **Raspberry Pi Zero 2 Wでも動きます。** 処理の中心はFlaskとAPIへのHTTP通信で、重い計算は
> ありません。GUIデスクトップも不要なので、Zero 2 Wでは「Raspberry Pi OS Lite (64-bit)」がおすすめです。

## セットアップ

```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y python3-full python3-venv git

# このリポジトリを取得し、この「1.Raspberry Pi版」フォルダで作業する
git clone <このリポジトリのURL>
cd <リポジトリ名>/1.Raspberry\ Pi版

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements_web.txt

cp .env.example .env
nano .env        # 使うAIのAPIキーを記入(使わないAIは空欄のままでOK)

python3 app.py
```

> Bookworm以降はPythonパッケージをOS全体に直接入れられない(PEP 668)ため、仮想環境(venv)を使います。

起動したら、同じLAN内のPC・スマホのブラウザで `http://<PiのIPアドレス>:5000` を開きます。
PiのIPアドレスは、Pi上で `hostname -I` を実行すると分かります。

> `.env` にはAPIキーが入ります。**GitHubなどに公開しないでください**(`.gitignore` で除外済みです)。

## 画面の使い方

1. 「会話のテーマ」を入力します(例: 生成AIは創造性を高めるか)。
2. 「参加させるAI」を**2体以上**選びます(キーが未設定のAIは選べません)。GPT・Claude・GLMは、
   使うモデルも選べます(一覧に無いモデルは「カスタム入力...」で直接指定)。Manusはモデル選択に非対応です。
3. 「モデレーター(司会役)」を、参加AIの中から1体選びます。司会はTurn 1で会話を切り出し、
   以降は連続しないようローテーションの中で発言します。
4. 「トーン」(真面目モード/フレンドリーモード)と「ターン数」(1〜20)を選びます。
5. **START** で開始。発言が吹き出しでリアルタイムに追加されます
   (Manus=緑・左、GPT=青・右、Claude=橙・中央、GLM=紫・左)。
6. **PAUSE / RESUME** は、次のターンの開始前に一時停止・再開します(API呼び出しの途中では止まりません)。
   **STOP / RESET** で中断します(中断時点までのログは保存されます)。
7. 2発言以上進んで会話が終わると、**要約**(全体の総括・AIごとの視点・主な論点・AI間で見解が分かれた点)が
   自動生成されます。要約とログ(`.txt` / `.json`)は、画面のリンクからダウンロードできます。

要約は、参加AIのうち JSON出力が安定している順(OpenAI → Claude → GLM → Manus)に選ばれた1体が作ります。
期待した形式で返らなかった場合は、AIの応答をそのまま表示します。

## 会話の流れ(START後)

1. 司会AIが、テーマについて開始の発言をする(Turn 1)。
2. 以降、司会**以外**のAI → … → 司会 の順に繰り返し発言する(指定ターン数まで)。
   各AIには「役割・トーンの指示」と「これまでの会話の全文」を毎回渡す
   (誰か1体だけが会話を覚えている、という状態にはならない)。
3. 発言のたびにイベントを配信し、ブラウザ/CrowPanelに表示する。
4. 指定ターン数(またはSTOP)で終了し、要約AIが要約を作る。ログを `logs/` に保存する。

## CrowPanel向けAPI

CrowPanelのように、長時間接続やチャンク解析が苦手なクライアントのため、ポーリング用のAPIがあります。
ブラウザ向けのSSEとは独立していて、互いに影響しません。

| エンドポイント | メソッド | 内容 |
| :--- | :--- | :--- |
| `/api/events?since=<seq>` | GET | `seq` より後のイベントを返す。`{"latest_seq": N, "events": [...]}` |
| `/api/start` | POST | 会話を開始(成功は202)。JSON: `topic` / `tone`("1"=真面目, "2"=フレンドリー) / `max_turns` / `participants`([{provider, model}]) / `moderator` |
| `/api/control` | POST | `{"action": "pause" \| "resume" \| "stop"}` |

`provider` は `manus` / `openai` / `claude` / `zai` のいずれかです。

イベントの種類: `session_start` / `status`(考え中・要約生成中) / `message` / `summary` / `error` / `session_end`。
イベント履歴は直近500件がメモリ上に保持されます。

## 自動起動(systemd・任意)

```bash
sudo nano /etc/systemd/system/ai-conversation-web.service
```

```ini
[Unit]
Description=AI Conversation Web UI
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=pi
WorkingDirectory=/home/pi/ai_conversation
ExecStart=/home/pi/ai_conversation/.venv/bin/python3 /home/pi/ai_conversation/app.py
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```

(ユーザー名・配置場所は環境に合わせて書き換えてください。)

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now ai-conversation-web.service
journalctl -u ai-conversation-web.service -f    # ログの確認
```

## セキュリティと注意事項

- **認証・HTTPSはありません。** `0.0.0.0:5000` で待ち受けるので、**信頼できるローカルLAN内だけで**使い、
  インターネットへ公開しないでください。
- 会話のログ(テーマ・全発言・要約)は `logs/` に `.json` / `.txt` で保存され、**自動では削除されません。**
  画面右上の「保存済みログを全削除」ボタン(`/api/clear_logs`)で、まとめて消せます。
- 各社のAPIは利用量に応じて課金されます。ご自身のキー・利用枠の範囲でお使いください。
- 同時に実行できる会話は1つです(2つ目のSTARTは409を返します)。
- 生成AIの発言内容は保証されません。

## トラブルシューティング

| 症状 | 対処 |
| :--- | :--- |
| ブラウザで開けない | Piの IP が変わっていないか(`hostname -I`)、PCとPiが同じLANか確認 |
| AIのチェックボックスが押せない | そのAIのキーが `.env` で空欄。行の右に「未設定」と出ます |
| `ModuleNotFoundError: No module named 'flask'` など | `.venv` を有効化していない(`source .venv/bin/activate`)、または `pip install -r requirements_web.txt` 未実施 |
| 「既に会話が実行中です」 | 前の会話が終わっていない。STOPするか、`app.py` を再起動 |
| 「Manus が考え中...」のまま動かない | Manusの応答が重い可能性。最大300秒(`POLL_TIMEOUT`)でタイムアウトします |
| 発言が「(…KEYが設定されていません)」になる | `.env` のキーが空欄、または無効 |
| 会話は進むのに表示が更新されない | ページ上部の接続ドットが灰色ならSSEが切れています。再読み込みしてください |

## ファイル構成

```
1.Raspberry Pi版/
├── app.py                   Flaskアプリ本体(ルーティング・SSE配信・会話スレッド管理)
├── conversation_engine.py   各AIの呼び出し、モデレーター/ローテーション制御、要約生成
├── broadcaster.py           SSE / ポーリング両対応のPub/Sub配信クラス
├── templates/index.html     Web UI(単一HTML。CSS/JS込み)
├── requirements_web.txt     依存パッケージ
├── .env.example             APIキー設定のテンプレート
└── logs/                    会話ログの保存先
```
