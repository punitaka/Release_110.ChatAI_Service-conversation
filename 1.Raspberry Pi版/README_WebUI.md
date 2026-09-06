# Raspberry Pi Web UI版 導入手順書(Manus × GPT × Claude 対応)

**対象OS**: Raspberry Pi OS 64bit(Debian Bookworm ベース)
**更新日**: 2026-09-02

---

## 0. この手順書の位置づけ

CrowPanel(ESP32-P4)側の実装が難航しているため、まずRaspberry Pi単体で完結する形に
方針転換しています。会話には **Manus / GPT(OpenAI) / Claude(Anthropic)** の中から
2〜3体を選んで参加させることができ、そのうち1体を **モデレーター(司会・進行役)** として
指定します。テーマ・参加AI・各AIのモデル・トーン・ターン数はすべてブラウザから指定します。

- 会話の進行(発言・「思考中」ステータス・エラー)はブラウザにリアルタイム表示されます。
- START / PAUSE / RESUME / STOP をブラウザのボタンで操作できます。
- 参加する全AIに、それまでの会話の全文脈が毎ターン渡されます(誰か1体だけが会話を
  覚えている、という非対称な状態にはなりません)。
- Manus/OpenAI/ClaudeのAPIキーはRaspberry Pi上の`.env`にのみ保持され、ブラウザには
  一切送信されません。

**データ保持についての注意**: 会話のテーマ・全発言・要約は、DBには保存していませんが、
`~/ai_conversation/logs/`に`.json`/`.txt`として**自動保存され、自動削除の仕組みはありません**
(無期限に残ります)。経営層への貸し出しなど機密性が求められる用途では、画面右上の
**「🗑 保存済みログを全削除」**ボタンで、保存済みログをまとめて削除できます(4章参照)。
現状はプロトタイプとしての割り切りで、削除運用は利用者の判断に委ねています。HTTPS化・
アクセス認証などの本格的なセキュリティ対応は、本採用が決まった段階で別途必要です。

現時点でRaspberry Piへの導入は未実施とのことなので、本書は「ゼロからのセットアップ」を
前提にしています。

---

## 1. 必要なもの

| 項目 | 内容 |
| :--- | :--- |
| **ハードウェア** | Raspberry Pi(Zero 2 W / 3B+ / 4 / 5 いずれも可)、電源、microSDカード |
| **OS** | Raspberry Pi OS 64bit(Bookworm以降) |
| **Python** | 3.8以上(OS標準で付属) |
| **ネットワーク** | インターネット接続(Wi-Fiまたは有線) |
| **Manus API Key** | [Manusダッシュボード](https://manus.im/app?show_settings=integrations&app_name=api) から取得 |
| **OpenAI API Key** | [OpenAI Platform](https://platform.openai.com/api-keys) から取得 |
| **Anthropic (Claude) API Key** | [Anthropic Console](https://console.anthropic.com/settings/keys) から取得 |

> **Raspberry Pi Zero 2 Wについて**: 本システムはPython(Flask)によるWebサーバーと、
> Manus/OpenAI/ClaudeへのHTTPリクエスト送受信が処理の中心で、ローカルでの重い計算
> (画像処理・ローカルLLM実行など)は行いません。そのため、クアッドコアCPU・512MB RAM
> のZero 2 Wでも十分に動作します。GUIデスクトップは不要(操作はすべてブラウザ経由)なため、
> Zero 2 Wで使う場合は「Raspberry Pi OS Lite (64-bit)」を選ぶと、限られたメモリを
> 有効に使えます。

3種類のAPIキーは、**必ずしも全部揃える必要はありません。** 参加させたいAIの分だけ
用意すれば動作します(例: Manus + Claudeの2体だけで会話させたい場合、OpenAIのキーは
空欄のままで構いません)。

---

## 2. OSを再インストールすべきか

**結論: 必須ではありません。** 既にRaspberry Pi OSが動いていれば、3章の手順をそのまま
実行するだけで構築できます。

**それでも新規インストールを検討してよい場面:**

- YouTube動画の「視聴者が最初から真似できる状態」を録画したい場合(まっさらな状態からの
  セットアップ手順そのものをコンテンツにしたい場合)。
- 現在のPiに、今回と無関係な過去の試行錯誤(ファイル・設定)が残っており、一度整理したい場合。

新規インストールから始めたい場合は、巻末の**付録A**を先に実施してから3章に進んでください。

---

## 3. セットアップ手順

### 手順1: システムパッケージの更新

```bash
sudo apt update && sudo apt upgrade -y
```

### 手順2: Python仮想環境ツールのインストール

Raspberry Pi OS 64bit(Bookworm)では、PythonパッケージをOS全体に直接インストールする
ことが制限されています(PEP 668)。そのため仮想環境(venv)を使います。

```bash
sudo apt install -y python3-full python3-venv
```

### 手順3: 作業ディレクトリの作成とファイルの配置

```bash
mkdir -p ~/ai_conversation
cd ~/ai_conversation
```

このリポジトリの `1.Raspberry Pi/` フォルダにある以下のファイルを、`~/ai_conversation/`
直下に配置してください。`templates/index.html` は `templates` フォルダごとコピーします。

```
~/ai_conversation/
├── app.py
├── conversation_engine.py
├── broadcaster.py
├── templates/
│   └── index.html
├── requirements_web.txt
├── .env.example
└── ai_orchestrator.py       (任意。旧・コンソール版。無くても動作します)
```

USBメモリ・`scp`・`git clone`など、普段お使いの方法で転送してください。`scp`を使う例:

```bash
# Windows側(PowerShell)から実行する例。<pi-ip>はご自身の環境に置き換えてください。
scp app.py conversation_engine.py broadcaster.py requirements_web.txt .env.example pi@<pi-ip>:~/ai_conversation/
scp -r templates pi@<pi-ip>:~/ai_conversation/
```

### 手順4: 仮想環境の作成と有効化

```bash
python3 -m venv .venv
source .venv/bin/activate
```

プロンプトの先頭に `(.venv)` が表示されれば成功です。

### 手順5: 必要なライブラリのインストール

```bash
pip install -r requirements_web.txt
```

`requirements_web.txt` に必要なパッケージ(`requests` / `openai` / `python-dotenv` / `flask` /
`anthropic`)がすべて記載されているので、このコマンド1つで完結します。`openai`は1.0以上を
指定しています。1.0未満(0.x系、古い呼び出し方の世代)が入ると、本プログラムが使っている
`from openai import OpenAI`という1.0系以降の書き方と噛み合わず動かなくなるためです。

### 手順6: APIキーの設定

```bash
cp .env.example .env
nano .env
```

Manus・OpenAI・Anthropic(Claude)の3つのAPIキーを記入します。**参加させないAIのキーは
空欄のままで構いません**(そのAIをチェックボックスで選ばなければ使われません)。

```text
MANUS_API_KEY=sk-xxxxxxxxxxxxxxxxxx
OPENAI_API_KEY=sk-xxxxxxxxxxxxxxxxxx
ANTHROPIC_API_KEY=sk-ant-xxxxxxxxxxxxxxxxxx
```

- Manus: https://manus.im/app?show_settings=integrations&app_name=api
- OpenAI: https://platform.openai.com/api-keys
- Anthropic(Claude): https://console.anthropic.com/settings/keys

保存は **Ctrl + X → Y → Enter** です。

> **セキュリティに関する注意**: `.env`ファイルにはAPIキーが含まれます。GitHubなど
> 公開の場所にアップロードしないよう注意してください。

### 手順7: 起動

```bash
cd ~/ai_conversation
source .venv/bin/activate
python3 app.py
```

以下のようなログが出れば起動成功です。

```
 * Running on all addresses (0.0.0.0)
 * Running on http://127.0.0.1:5000
 * Running on http://<PiのIPアドレス>:5000
```

### 手順8: ブラウザからアクセス

同じLAN(Wi-Fi/有線)につながっているPCやスマホのブラウザで、

```
http://<PiのIPアドレス>:5000
```

を開きます。PiのIPアドレスが分からない場合は、Pi上で以下を実行して確認できます。

```bash
hostname -I
```

---

## 4. 画面の使い方

1. 「会話のテーマ」欄にテーマを入力します(例: 生成AIは創造性を高めるか)。
2. 「参加させるAI」で、Manus / GPT(OpenAI) / Claude(Anthropic) の中から**2〜3体**に
   チェックを入れます。APIキーが`.env`に設定されていないAIはチェックできません。
   GPT・Claudeを選んだ場合は、隣のプルダウンで使用するモデルも選びます
   (一覧に無いモデル名を使いたい場合は「カスタム入力...」を選んで直接入力できます)。
   Manusはモデル選択に対応していません。
3. 「モデレーター(司会役)」で、チェックした参加AIの中から1体を司会役として選びます。
   モデレーターはTurn1で会話を切り出し、以降もローテーションの中で(連続しないように)
   発言し続けます。
4. 「トーン」で真面目モード/フレンドリーモードを選びます。
5. 「ターン数」を1〜20の範囲で指定します(既定値6)。
6. **START** を押すと会話が始まり、各AIの発言が吹き出し形式でリアルタイムに追加されます
   (Manus=緑・左寄せ、GPT=青・右寄せ、Claude=橙・中央)。
7. **PAUSE** で次のターン開始前に一時停止、**RESUME** で再開します(API呼び出しの途中では
   止まらず、区切りの良いタイミングで一時停止します)。
8. **STOP / RESET** で会話を中断します。中断時点までの会話ログは自動保存されます。
9. 会話が2発言以上進んだ状態で終了(完了または途中停止)すると、**会話全体の要約**が
   自動生成されます。「会話の要約」欄に、以下の内容が表示されます。
   - 会話全体の総括(2〜3文)
   - **参加AIごとの視点・論点の箇条書き**(発言者の色で見出しを色分け)
   - 会話全体を通じて浮かび上がった主な論点・課題の一覧
   - **AI間で見解が分かれた点**(赤系の枠で表示。単なる言い回しの違いではなく、
     実質的に主張・評価が異なった箇所のみを抽出します。見解の相違が無ければ表示されません)
10. 要約の下に **「要約・ログをダウンロード(.txt)」「詳細ログ(.json)」** のリンクが表示されます。
    クリックすると、ブラウザ経由でそのままファイルをダウンロードできます
    (Piにログインする必要はありません)。`.txt`は要約を含む読みやすい形式、`.json`は
    発言データを構造化した詳細ログです。ログは`~/ai_conversation/logs/`にも自動保存されます。

要約は、参加AIの中からJSON出力の安定性が高い順(OpenAI→Claude→Manus)に自動選択された
1体が生成します。まれに要約が期待した形式で出力されない場合は、生成AIの応答をそのまま
表示するフォールバック表示になります(要約自体が失われることはありません)。

ブラウザを閉じても、Pi側の会話生成は止まりません。別のブラウザ・別の端末から同じURLに
再度アクセスすれば、その時点からの進行状況をリアルタイムで見られます(過去の発言は
再接続時には再送されないため、複数タブは同時に開いたまま使うことを推奨します)。

### ログの一括削除

画面右上の **「🗑 保存済みログを全削除」** ボタンを押すと、`~/ai_conversation/logs/`に
保存されている会話ログ(`.json`/`.txt`)をすべて削除できます。確認ダイアログが出るので、
問題なければOKを押してください。削除後は「N件のファイルを削除しました」と表示されます。

- 実行中の会話には影響しません(削除されるのは過去に保存済みのログファイルのみです)。
- 取り消しはできません。経営層への貸し出し等、機密性が求められる場面では、デモの前後に
  押しておくことを推奨します。
- `logs/`フォルダ内の`conversation_`で始まるファイル以外(他の用途で置いたファイル等)は
  削除対象になりません。

---

## 5. (任意) 自動起動させたい場合(systemdサービス化)

動画撮影時にPiの電源を入れるだけで自動的にWeb UIが起動しているようにしたい場合は、
systemdサービスとして登録できます。

```bash
sudo nano /etc/systemd/system/ai-conversation-web.service
```

以下を貼り付けます(ユーザー名が`pi`以外の場合は`User=`と`WorkingDirectory=`を書き換えてください)。

```ini
[Unit]
Description=Manus x GPT x Claude AI Conversation Web UI
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

有効化と起動:

```bash
sudo systemctl daemon-reload
sudo systemctl enable ai-conversation-web.service
sudo systemctl start ai-conversation-web.service
```

ログの確認:

```bash
journalctl -u ai-conversation-web.service -f
```

停止・無効化したい場合:

```bash
sudo systemctl stop ai-conversation-web.service
sudo systemctl disable ai-conversation-web.service
```

---

## 6. トラブルシューティング

| 症状 | 原因と対処法 |
| :--- | :--- |
| ブラウザで開いても「このサイトにアクセスできません」 | PiのIPアドレスが変わっている(`hostname -I`で再確認)。PCとPiが同じLANに接続されているか確認。 |
| 参加させたいAIのチェックボックスが押せない | そのAIの`.env`キー(`MANUS_API_KEY`/`OPENAI_API_KEY`/`ANTHROPIC_API_KEY`)が空欄。行の右側に「未設定」の赤字が出ていないか確認。 |
| STARTを押しても「テーマを入力してください」等のアラートが出る | フォームの必須項目(テーマ・参加AI2〜3体・モデレーター・各AIのモデル)が未入力です。 |
| STARTを押しても反応がない/エラーが出る | ターミナル側(`python3 app.py`を実行している画面)にエラーログが出ていないか確認してください。 |
| 「既に会話が実行中です」と出る | 前回の会話がまだ実行中(または異常終了せず残っている)。STOPを押すか、`python3 app.py`を再起動してください。 |
| 「保存済みログを全削除」を押しても件数が0のまま | `logs/`フォルダに`conversation_`で始まるファイルが無い(まだ会話が1件も完了していない等)。 |
| ステータスが「Manus が考え中...」のまま長時間動かない | Manus側の応答が重い可能性があります。最大300秒(`conversation_engine.py`の`POLL_TIMEOUT`)で自動的にタイムアウトします。 |
| `ModuleNotFoundError: No module named 'flask'` または `'anthropic'` | `.venv`を有効化せずに実行している、または手順5のインストールを行っていません。 |
| Claudeの発言が「(ANTHROPIC_API_KEYが設定されていません)」になる | `.env`の`ANTHROPIC_API_KEY`が空欄、またはキーが無効です。 |
| 会話は進むがブラウザの表示が更新されない | ページ上部の接続ドットが灰色の場合、SSE接続が切れています。ページを再読み込みしてください(自動再接続もされます)。 |

---

## 7. ファイル構成

```
1.Raspberry Pi/
├── app.py                   # Flaskアプリ本体(ルーティング・SSE配信・会話スレッド管理)
├── conversation_engine.py   # Manus/GPT/Claude呼び出しロジック、モデレーター/ローテーション制御
├── broadcaster.py           # SSEのPub/Sub配信クラス
├── templates/
│   └── index.html           # Web UI本体(単一HTMLファイル、CSS/JS込み)
├── requirements_web.txt     # 必要なパッケージ一覧(これ1つで pip install -r 完結)
├── README_WebUI.md          # 本ファイル(導入手順書)
├── ai_orchestrator.py       # 旧版のシンプルなコンソールスクリプト(参考用。無くても本システムの動作に影響しません)
├── .env / .env.example
└── logs/
```

---

## 8. 次のステップ

CrowPanelの実装を再開する際は、`broadcaster.py`の`Broadcaster`クラスをそのまま使い、
ブラウザ向けのSSE配信と並行して、CrowPanelとのWebSocket配信を追加する形で拡張できます
(発行するイベントのJSON形式は既に共通設計にしてあります)。Web UIとCrowPanelを
同時に接続した状態で、両方に同じ会話がリアルタイム表示される構成が最終形です。

---

## 付録A: まっさらなRaspberry Pi OSから始める場合

YouTube動画用にゼロから録画したい場合の手順です。

1. PCで **Raspberry Pi Imager** ([https://www.raspberrypi.com/software/](https://www.raspberrypi.com/software/)) を起動。
2. デバイス: お使いのRaspberry Piの機種を選択。
3. OS: 「Raspberry Pi OS (64-bit)」を選択(Bookwormベース)。
4. ストレージ: 書き込み先のmicroSDカードを選択。
5. 歯車アイコン(詳細設定)を開き、以下を事前設定しておくと、モニタなしでSSH接続できます(ヘッドレスセットアップ)。
   - ホスト名の設定
   - SSHを有効化(パスワード認証、またはご自身の公開鍵を登録)
   - ユーザー名とパスワード
   - Wi-FiのSSID/パスワード、国コード
6. 書き込み完了後、microSDをPiに挿して起動。数分待ってからPCから接続します。

```bash
ssh <設定したユーザー名>@<設定したホスト名>.local
```

7. 接続できたら、本書の3章(手順1)から進めてください。
