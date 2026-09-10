#!/bin/bash

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR" || exit 1

echo "Fashion Press Tokyo Brand Overview Scraper"
echo "=========================================="
echo ""

if ! command -v python3 >/dev/null 2>&1; then
  echo "python3 が見つかりません。"
  echo "先に Python 3 をインストールしてください。"
  echo ""
  echo "ダウンロード先: https://www.python.org/downloads/"
  echo ""
  read -r -p "Enterキーで閉じます..."
  exit 1
fi

python3 fashion_press_collections_scraper.py

echo ""
echo "終了しました。"
read -r -p "Enterキーで閉じます..."
