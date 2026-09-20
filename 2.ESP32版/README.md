# ESP32版 ── CrowPanel Advanced 7inch 用 AI会話ビューア

[CrowPanel Advanced 7inch(ESP32-P4)](https://www.elecrow.com/) のタッチ画面から、
Raspberry Pi 上の会話サーバー([`../1.Raspberry Pi版/`](../1.Raspberry%20Pi版/))を操作し、
AI同士の会話をLINE風の吹き出しで表示するファームウェアです。

**役割分担**

- **CrowPanel**: 設定・表示・操作(START / 一時停止 / 再開 / 停止)。APIキーは持ちません。
- **Raspberry Pi**: 各AIのAPIを呼ぶ。キーはPiの `.env` にだけ置きます。
- 通信は、同じLAN上のHTTP(Wi-Fi)です。CrowPanelは約2秒ごとに `/api/events` をポーリングします。

## 画面

1. **設定画面**: 参加AI(Manus / GPT / Claude / GLM のチェックボックス、2〜4体)、モデレーター、
   トーン、ターン数、テーマ(固定候補から選ぶ、または英語で自由入力)を選んで「開始」。
2. **会話画面**: 発言が吹き出しで流れます(Manus=緑・左、GPT=青・右、Claude=橙・中央、GLM=紫・左)。
   会話が終わると、要約(総括・AIごとの視点・論点・見解が分かれた点)を横幅いっぱいのパネルで表示します。
   下部の「一時停止/再開」「停止(終了後は「戻る」)」で操作します。

## 動作確認環境

| 項目 | 内容 |
| :--- | :--- |
| ボード | CrowPanel Advanced 7inch **V1.1**(ESP32-P4 rev v1.3 + ESP32-C6 / 1024×600) ※V1.2は未確認 |
| ESP-IDF | **v5.5.5** |
| LVGL | 8.4.0(`lvgl/lvgl ^8.3.11`。依存は `main/idf_component.yml` と `dependencies.lock`) |
| サーバー | このリポジトリの `1.Raspberry Pi版`(Raspberry Pi 5 / Bookworm) |

> 無線(Wi-Fi)を使います。お住まいの国・地域の電波法規への適合(日本では技適)は、
> ご自身で確認してください。

## ビルドと書き込み

**0. Piのサーバーを起動しておく**(`../1.Raspberry Pi版/README.md`)。

**1. ELECROWの公式サンプルから、ボード依存コンポーネントを取得する**

`bsp_display` / `bsp_i2c` / `bsp_wifi` は、ELECROW社のコードで、公式リポジトリにライセンス表記が無いため
このリポジトリには含まれていません([`components/README.md`](components/README.md))。

```bash
python tools/fetch_elecrow_bsp.py
```

**2. ESP-IDF環境で、ターゲットを設定する**

```bash
idf.py set-target esp32p4
```

`sdkconfig.defaults` / `sdkconfig.defaults.esp32p4` が読み込まれ、次の設定が自動で入ります
(チップリビジョンv1.3対応、4-bit SDIO、Flash 16MB/QIO、PSRAM 32MB、LVGLメモリなど。
理由は `sdkconfig.defaults.esp32p4` のコメント参照)。

**3. Wi-Fi と PiのURL を設定する**

```bash
idf.py menuconfig
```

`AI Conversation (CrowPanel) Settings` で、次の3つを設定します。

| 項目 | 例 |
| :--- | :--- |
| Wi-Fi SSID | 自宅のWi-Fi。**2.4GHzのみ**(基板のESP32-C6は5GHz非対応) |
| Wi-Fi password | |
| Raspberry Pi server URL | `http://192.168.0.10:5000`(Pi上で `hostname -I` で確認) |

> これらの値は、ソースではなく生成物の `sdkconfig` に保存されます。`sdkconfig` は `.gitignore` 済みです。
> 書き込んだファームウェアにもWi-Fiパスワードが平文で入るため、基板の取り扱いにはご注意ください。

**4. ビルド・書き込み・ログ確認**

```bash
idf.py -p COM3 build flash monitor      # COM3は環境に合わせて変更(Linux/macOSは /dev/ttyUSB0 等)
```

起動後、Wi-Fiにつながると設定画面に「開始」ボタンが現れます。

## スプラッシュ画像(任意)

起動時に、好きな画像を約3秒表示できます(既定では無効)。

```bash
pip install Pillow
python tools/make_splash_bin.py my_logo.png
idf.py build flash
```

`main/splash_logo.bin`(1024×600のRGB565生データ、約1.2MB)ができると、自動でFlashに埋め込まれます。
消せば無効に戻ります(コードの変更は不要)。他社のロゴ等を含む画像の権利はご自身で確認してください。

## 日本語表示について

日本語フォントは、常用漢字2,136字+かな+記号を収録したビットマップフォントをFlashに埋め込んでいます
(詳細・作り直し方は [`main/fonts/README.md`](main/fonts/README.md))。

- **常用漢字外の漢字(例: 惹)や絵文字は、□(豆腐)で表示されます。**
- 自由入力のテーマは**英語のみ**です(オンスクリーンキーボードがIME非対応のため)。日本語のテーマは固定候補から選びます。
- 収録字数を増やすとFlash読み出しが増え、描画が不安定になる症状が出ました(下記「既知の課題」)。

## トラブルシューティング

| 症状 | 原因と対処 |
| :--- | :--- |
| 書き込み時に `requires chip revision in range [v3.1 - v3.99] (this chip is revision v1.3)` | `set-target` の前に `sdkconfig` が古い設定で作られている。`sdkconfig` を削除して `idf.py set-target esp32p4` からやり直す |
| `Could not open COMx, the port is busy or doesn't exist` | USBケーブルの**データ線の劣化**が意外に多い(給電はできて基板は動くのに、PCがCOMポートを認識できない)。別のケーブル・別のUSBポートを試す。デバイスマネージャーに古いCOMが多数残っていれば削除 |
| COMポートが2つ見える(CH340 と USBシリアルデバイス) | この基板は2系統のUSBシリアルを持つ。書き込みが失敗する場合は、もう一方のポートも試す(CH340側は書き込み用の専用チップ) |
| 画面の周囲が白くちらつく・震える | **ディスプレイのフレキシブルケーブルの接続が緩んでいる**ことがあった(コネクタを差し直して解消)。ソフトを疑う前に物理接続を確認 |
| Wi-Fiにつながらない | SSID/パスワード、**2.4GHz**であることを確認。`idf.py monitor` のログを見る |
| 「開始」を押すと失敗する | PiのURL(IPアドレス)が正しいか、Pi側サーバーが起動していてPCから開けるか確認 |
| スクロール中にリセットされる | タスクウォッチドッグ。`sdkconfig.defaults.esp32p4` の `CONFIG_ESP_TASK_WDT_TIMEOUT_S` と、`main.c` の `LOG_MAX_LINES` を参照 |

## 既知の課題

- **フォントの収録字数を増やすと、画面周囲がちらつく症状が出た。** JIS第一+第二水準(6,358字)版で確認し、
  常用漢字2,136字に戻すと解消しました。原因は調査中です(ESP32-P4ではFlashとPSRAMがL2キャッシュを共有しており、
  その競合を疑っています)。ただし調査の途中で、上記のとおりディスプレイのケーブル接続不良でも似た症状が出ると分かったため、
  ケーブルを確認した状態での再検証が必要です。対策候補は、L2キャッシュの拡大(内部RAMとのトレードオフあり)、
  SDカードからのフォント読み込み(`lv_font_load()`)、FreeTypeによるアウトライン描画などです。
- 絵文字・常用漢字外の漢字は表示できません。
- 認証・暗号化はありません(HTTP、LAN内利用が前提)。

## ファイル構成

```
2.ESP32版/
├── CMakeLists.txt
├── partitions.csv                 アプリ領域 約3.94MB x3(factory + OTA x2。OTAは未使用)
├── sdkconfig.defaults             (空。存在自体が必要)
├── sdkconfig.defaults.esp32p4     ボード向けの恒久設定
├── dependencies.lock              依存コンポーネントのバージョン固定
├── main/
│   ├── main.c                     UI(設定画面・会話画面)と全体の流れ
│   ├── Kconfig.projbuild          Wi-Fi / PiのURL の設定項目
│   ├── CMakeLists.txt
│   ├── idf_component.yml
│   └── fonts/                     日本語フォントとその作り方
├── components/
│   ├── app_conversation/          Piの /api/events・/api/start・/api/control を叩くHTTPクライアント
│   └── (bsp_display / bsp_i2c / bsp_wifi は tools/fetch_elecrow_bsp.py で取得)
└── tools/
    ├── fetch_elecrow_bsp.py       ELECROW公式サンプルから bsp_* を取得
    └── make_splash_bin.py         スプラッシュ画像の変換
```
