#!/usr/bin/env python3
"""
fetch_elecrow_bsp.py
====================
ELECROW公式サンプル(CrowPanel Advanced 7inch ESP32-P4, V1.1)の
`Lesson16_Get_weather_via_WiFi/components` から、本プロジェクトが使う3つの
ボード依存コンポーネントを components/ にコピーする。

    python tools/fetch_elecrow_bsp.py

    bsp_display   MIPI-DSIパネル(EK79007) + タッチ(GT911) + LVGL初期化
    bsp_i2c       I2Cバス初期化
    bsp_wifi      Wi-Fi(ESP32-C6, esp-hosted経由)の接続ヘルパー

これらはELECROW社のサンプルコードで、公式リポジトリにライセンス表記が無いため、
本リポジトリには同梱していません。各自で公式リポジトリから取得してください
(本スクリプトは、公式リポジトリから必要なフォルダだけを git のスパースチェックアウトで
取得するのを自動化するだけです。公式リポジトリは約10万ファイルあり、Windowsでは丸ごとの
クローンが「Filename too long」で失敗するため、この方法にしています)。
取得したコードの権利はELECROW社に帰属します。

https://github.com/Elecrow-RD/CrowPanel-Advanced-7inch-ESP32-P4-HMI-AI-Display-1024x600-IPS-Touch-Screen
"""

import pathlib
import shutil
import subprocess
import sys
import tempfile

REPO = "https://github.com/Elecrow-RD/CrowPanel-Advanced-7inch-ESP32-P4-HMI-AI-Display-1024x600-IPS-Touch-Screen.git"
SAMPLE = "example/V1.1/idf-code/Lesson16_Get_weather_via_WiFi/components"
COMPONENTS = ["bsp_display", "bsp_i2c", "bsp_wifi"]

DEST = pathlib.Path(__file__).resolve().parent.parent / "components"


def main() -> int:
    if shutil.which("git") is None:
        print("git が見つかりません。インストールしてから再実行してください。", file=sys.stderr)
        return 1

    with tempfile.TemporaryDirectory() as tmp:
        clone = pathlib.Path(tmp) / "elecrow"
        print("ELECROW公式リポジトリから必要なフォルダだけを取得中...")
        subprocess.run(["git", "clone", "--depth", "1", "--filter=blob:none", "--sparse",
                        REPO, str(clone)], check=True)
        subprocess.run(["git", "-C", str(clone), "sparse-checkout", "set", SAMPLE], check=True)
        src_root = clone / SAMPLE
        for name in COMPONENTS:
            src = src_root / name
            if not src.is_dir():
                print(f"見つかりません: {src}(公式リポジトリの構成が変わった可能性があります)",
                      file=sys.stderr)
                return 1
            dst = DEST / name
            if dst.exists():
                shutil.rmtree(dst)
            shutil.copytree(src, dst)
            print(f"copied: components/{name}")
    print("完了。続けて README の手順(idf.py set-target esp32p4 など)に進んでください。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
