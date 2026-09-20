#!/usr/bin/env python3
"""
make_splash_bin.py
==================
起動時に表示するスプラッシュ画像(任意)を、ファームウェアに埋め込める
生データ main/splash_logo.bin に変換する。

    pip install Pillow
    python tools/make_splash_bin.py my_logo.png

- 画面(1024x600)に合わせてリサイズし、LVGLの色設定(LV_COLOR_DEPTH=16, LV_COLOR_16_SWAP=0)
  に合うRGB565・リトルエンディアンで書き出す。
- 出力先 main/splash_logo.bin があると、main/CMakeLists.txt が自動でFlashに埋め込み、
  起動時に約3秒表示する。ファイルを削除すれば無効になる(コードの変更は不要)。
- サイズは 1024*600*2 = 1,228,800バイト(約1.2MB)。
- 画像の権利(他社のロゴ等を含む場合は特に)は、ご自身で確認してください。
"""

import argparse
import pathlib
import sys

from PIL import Image

WIDTH, HEIGHT = 1024, 600
DEFAULT_OUT = pathlib.Path(__file__).resolve().parent.parent / "main" / "splash_logo.bin"


def convert(src: pathlib.Path, dst: pathlib.Path) -> int:
    im = Image.open(src).convert("RGB").resize((WIDTH, HEIGHT), Image.LANCZOS)
    px = im.load()
    buf = bytearray(WIDTH * HEIGHT * 2)
    i = 0
    for y in range(HEIGHT):
        for x in range(WIDTH):
            r, g, b = px[x, y]
            rgb565 = ((r >> 3) << 11) | ((g >> 2) << 5) | (b >> 3)
            buf[i] = rgb565 & 0xFF          # 下位バイトが先(リトルエンディアン)
            buf[i + 1] = (rgb565 >> 8) & 0xFF
            i += 2
    dst.write_bytes(buf)
    return len(buf)


def main() -> int:
    ap = argparse.ArgumentParser(description="画像を 1024x600 RGB565 の splash_logo.bin に変換する")
    ap.add_argument("image", type=pathlib.Path, help="元画像(PNG/JPEG等)")
    ap.add_argument("-o", "--output", type=pathlib.Path, default=DEFAULT_OUT,
                    help=f"出力先(既定: {DEFAULT_OUT})")
    args = ap.parse_args()

    if not args.image.is_file():
        print(f"画像が見つかりません: {args.image}", file=sys.stderr)
        return 1
    size = convert(args.image, args.output)
    print(f"wrote {args.output} ({size:,} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
