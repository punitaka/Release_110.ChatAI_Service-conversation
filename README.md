# Manus × GPT × Claude AI会話システム(Raspberry Pi版)

[![Raspberry Pi](https://img.shields.io/badge/Raspberry_Pi-Zero_2_W_%2F_5-A22846?logo=raspberrypi&logoColor=white)](https://www.raspberrypi.com/)
[![Python](https://img.shields.io/badge/Python-3.11-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![Flask](https://img.shields.io/badge/Flask-Web_UI-000000?logo=flask&logoColor=white)](https://flask.palletsprojects.com/)
[![OpenAI](https://img.shields.io/badge/OpenAI-GPT-412991?logo=openai&logoColor=white)](https://platform.openai.com/docs)
[![Anthropic](https://img.shields.io/badge/Anthropic-Claude-D97757?logo=anthropic&logoColor=white)](https://docs.anthropic.com/)
[![Manus](https://img.shields.io/badge/Manus-API_v2-6B46C1)](https://open.manus.ai/docs/v2/introduction)
[![YouTube](https://img.shields.io/badge/YouTube-解説動画-FF0000?logo=youtube&logoColor=white)](https://www.youtube.com/@regional-engineer)

Raspberry Pi 1台の上で、**Manus / GPT(OpenAI) / Claude(Anthropic)** という3種類の生成AIに、
指定したテーマについて会話させるWebアプリケーションです。ブラウザからテーマ・参加AI・モデレーター
(司会役)を指定して開始すると、AI同士がターン制で会話を進め、終了後には各AIの視点や
「AI間で見解が分かれた点」を自動要約します。

もともとはCrowPanel(ESP32-P4)のディスプレイに会話を表示する構想の一部でしたが、
CrowPanel側の実装が難航したため、まずRaspberry Pi単体で完結するWeb UI版として先行公開しています。

> [!NOTE]
> 個人によるプロトタイプ開発です。Manus・OpenAI・Anthropicの各社とは無関係の非公式プロジェクトです。
> 本リポジトリのコード・ドキュメントの一部はAI(Claude / Manus)による生成物を含みます。内容の正確性は
> 保証しません。利用は自己責任でお願いします。

## 解説動画

<p align="center">
  <a href="https://youtu.be/XXXXXXXXXXX">
    <img src="https://img.youtube.com/vi/XXXXXXXXXXX/maxresdefault.jpg" alt="Manus x GPT x Claude AI会話システム 解説動画" width="720">
  </a>
</p>

<!-- 動画公開後、上記2箇所の XXXXXXXXXXX を実際のYouTube動画IDに差し替えてください -->

## できること

- Manus / GPT(OpenAI) / Claude(Anthropic) から**2〜3体を自由に選んで参加**させられる
- 参加AIのうち1体を**モデレーター(司会役)**に指定でき、Turn1で会話を切り出し、以降は
  モデレーターが連続発言しないようローテーションする
- OpenAI・Claudeは使用モデルを画面から選択可能(一覧に無いモデル名も直接入力できる)
- 参加する全AIに、それまでの会話の全文脈を毎ターン共有(誰か1体だけが会話を覚えている、
  という非対称な状態にはならない)
- 会話の進行(発言・「思考中」ステータス・エラー)をブラウザにリアルタイム表示
- START / PAUSE / RESUME / STOPをブラウザから操作可能
- 会話終了後、**参加AIごとの視点・主な論点・AI間で見解が分かれた点**を自動要約
- 要約とログ(`.txt` / `.json`)をブラウザから直接ダウンロード可能
- 画面から保存済みログを一括削除できるボタンを搭載(機密性が求められる用途を想定)
- Manus/OpenAI/AnthropicのAPIキーはRaspberry Pi上の`.env`にのみ保持し、ブラウザには一切送信しない

## ディレクトリ構成

```
1.Raspberry Pi版/
├── app.py                    Flaskアプリ本体(ルーティング・SSE配信・会話スレッド管理)
├── conversation_engine.py    Manus/GPT/Claude呼び出しロジック、モデレーター/ローテーション制御、要約生成
├── broadcaster.py            Server-Sent EventsのPub/Sub配信クラス
├── templates/
│   └── index.html            Web UI本体(単一HTMLファイル)
├── ai_orchestrator.py        旧版のシンプルなコンソールスクリプト(参考用)
├── requirements_web.txt      依存パッケージ一覧
├── .env.example               APIキー設定のテンプレート
├── README_WebUI.md            導入手順書(必要なもの・セットアップ・使い方・トラブルシューティング)
├── 【claude作成版】この仕組みの全体像.pptx   Claudeが作成した概要スライド
└── 【manus作成版】この仕組みの全体像.pptx    Manusが作成した概要スライド(同じ指示から作成し比較用)
```

## セットアップ

Raspberry Pi(Zero 2 W / 3B+ / 4 / 5)、Raspberry Pi OS 64bit(Bookworm以降)、
Manus / OpenAI / AnthropicのAPIキー(参加させたいAIの分だけでOK)が必要です。

```bash
mkdir -p ~/ai_conversation && cd ~/ai_conversation
# このリポジトリの「1.Raspberry Pi版/」直下のファイルをここに配置

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements_web.txt

cp .env.example .env
nano .env    # MANUS_API_KEY / OPENAI_API_KEY / ANTHROPIC_API_KEY を記入

python3 app.py
```

起動後、同じLAN内のブラウザから `http://<Raspberry PiのIPアドレス>:5000` にアクセスします。

**詳しい手順(付録: まっさらなOSからのセットアップ・systemd自動起動・トラブルシューティング含む)は
[`1.Raspberry Pi版/README_WebUI.md`](<1.Raspberry Pi版/README_WebUI.md>) を参照してください。**

## 制約と注意事項

- 個人開発のプロトタイプであり、HTTPS化・アクセス認証などは未実装です。信頼できるローカルLAN内での
  利用を想定しています。
- 会話ログ(テーマ・全発言・要約)はDBには保存していませんが、`logs/`フォルダに`.json`/`.txt`として
  自動保存され、自動削除の仕組みはありません。機密性が求められる用途では、画面の
  「保存済みログを全削除」ボタンをご利用ください。
- Manus / OpenAI / Anthropicの各APIは利用量に応じて課金されます。ご自身のAPIキー・利用枠の範囲で
  お使いください。
- 同時に実行できる会話は1つまでです。

## 関連リポジトリ

- [Release_100.CrowPanel_Advanced_Display](https://github.com/punitaka/Release_100.CrowPanel_Advanced_Display) ── CrowPanel(ESP32-P4)側の開発資料集。本プロジェクトはこの表示器へのAI会話表示を最終目標としています。

## 参考リンク

- [Manus API v2 Documentation](https://open.manus.ai/docs/v2/introduction)
- [OpenAI API Documentation](https://platform.openai.com/docs)
- [Anthropic API Documentation](https://docs.anthropic.com/)
- [Raspberry Pi公式サイト](https://www.raspberrypi.com/)
- YouTube: [@regional-engineer](https://www.youtube.com/@regional-engineer)
