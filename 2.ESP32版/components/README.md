# components/

| ディレクトリ | 内容 | 入手方法 |
| :--- | :--- | :--- |
| `app_conversation/` | Raspberry Pi の `/api/events`(ポーリング)・`/api/start`・`/api/control` を叩くHTTPクライアント(本プロジェクト作成) | このリポジトリに含まれる |
| `bsp_display/` | MIPI-DSIパネル(EK79007)・タッチ(GT911)・LVGL初期化 | **ELECROW公式サンプルから取得**(下記) |
| `bsp_i2c/` | I2Cバス初期化 | 同上 |
| `bsp_wifi/` | Wi-Fi(ESP32-C6, esp-hosted経由)の接続ヘルパー | 同上 |

## `bsp_*` の取得(必須)

`bsp_display` / `bsp_i2c` / `bsp_wifi` は、ELECROW社の公式サンプル
([CrowPanel Advanced 7inch ESP32-P4 のリポジトリ](https://github.com/Elecrow-RD/CrowPanel-Advanced-7inch-ESP32-P4-HMI-AI-Display-1024x600-IPS-Touch-Screen)、
`example/V1.1/idf-code/Lesson16_Get_weather_via_WiFi/components/`)に含まれるコードです。
公式リポジトリにライセンス表記が無いため、**このリポジトリには同梱していません**。
次のコマンドで、必要な3つだけを公式リポジトリから取得できます(`git` が必要です)。

```bash
python tools/fetch_elecrow_bsp.py
```

取得したコードの権利はELECROW社に帰属します。手動でコピーする場合は、上記パスの
`bsp_display` / `bsp_i2c` / `bsp_wifi` の3フォルダをこのディレクトリに置いてください。
