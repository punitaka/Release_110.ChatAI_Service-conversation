# 日本語フォント

| ファイル | 内容 |
| :--- | :--- |
| `notosans_jp_20.c` | 実際に使っているフォント(LVGL用のC配列)。NotoSansJP-Bold、20px、4bpp。ASCII + 日本語記号 + ひらがな + カタカナ + 全角形 + **常用漢字2,136字** を収録 |
| `kanji_symbols_jis_level1_2.txt` | 収録字数を増やす実験用の字リスト。JIS第一水準+第二水準(6,355字)+ 常用漢字表内でJISに無い3字(剝・塡・頰)= 計6,358字 |

## フォントの出典とライセンス

`notosans_jp_20.c` は、[Noto Sans JP](https://fonts.google.com/noto/specimen/Noto+Sans+JP)
(© The Noto Project Authors、[SIL Open Font License 1.1](https://openfontlicense.org/))から、
LVGLのフォントコンバータで必要な文字だけをビットマップ化した派生物です。
OFL 1.1に従い、上記の著作権表示とライセンスを明記したうえで再配布しています
(フォント単体での販売はしません)。

## フォントの作り直し方

1. [LVGL Font Converter](https://lvgl.io/tools/fontconverter) を開く
2. 次のように設定する
   - Name: `notosans_jp_20` / Size: `20` / Bpp: `4`
   - Font: `NotoSansJP-Bold.ttf`(Google Fontsからダウンロード)
   - Range: `0x20-0x7E, 0x3000-0x303F, 0x3040-0x309F, 0x30A0-0x30FF, 0xFF00-0xFFEF`
   - Symbols: 収録したい漢字を並べた文字列(例: 上記の字リストの中身を貼り付け)
3. 出力された `.c` を `notosans_jp_20.c` として置き換え、次の2点を直す
   - `.static_bitmap = 0,` の行を**削除**(このプロジェクトが使うLVGL 8.x の `lv_font_t` に該当フィールドが無く、
     コンパイルエラーになるため)
   - `.fallback = NULL,` を `.fallback = LV_FONT_DEFAULT,` に変更(LVGL内蔵のシンボル
     ▼ 等がドロップダウンで表示されるようにするため)

## 既知の制約

- **文字数を増やすと描画が不安定になることがある**: JIS第一+第二水準(6,358字)版を試したところ、
  画面周囲が白くちらつく症状が出ました(常用漢字2,136字に戻すと解消)。原因は調査中です
  (ESP32-P4ではFlashとPSRAMがL2キャッシュを共有しており、その競合を疑っています)。
  試す場合は `sdkconfig.defaults.esp32p4` の `CONFIG_LV_FONT_FMT_TXT_LARGE=y` が必要です。
- **常用漢字外の漢字(例: 惹)や絵文字は、□(豆腐)で表示されます。**
