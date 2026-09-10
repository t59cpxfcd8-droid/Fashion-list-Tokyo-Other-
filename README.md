# Fashion Press Tokyo Brand Overview Scraper

Fashion Press のコレクション一覧から、東京に表示されているブランド分だけブランド概要を取得するGUIツールです。

## 取得対象

- 対象サイト: https://www.fashion-press.net/collections/
- 場所: 東京固定
- 範囲: 選択した各シーズンの東京一覧1ページ目に表示されているブランドのみ
- 次ページ巡回: なし

## CSV列

```text
ブランド, ブランド（カタカナ）, ブランドURL, ブランド概要
```

## 実行方法

Macでは `FashionPressScraper.command` をダブルクリックすると起動できます。

ターミナルから実行する場合:

```bash
python3 fashion_press_collections_scraper.py
```

## お客様PCに必要なもの

- Python 3: 必要
- VS Code: 実行だけなら不要。コードを編集する場合のみあると便利

Macの場合、Windowsの `.bat` の代わりに `.command` ファイルを使います。

## サーバー負荷対策

- 一覧ページ取得ごとに5秒待機
- ブランド詳細ページ取得ごとに6秒待機
- 失敗時は待機してから最大3回まで再試行
- 同じブランドURLは重複取得しない
- 各シーズンの1ページ目だけを対象にして、ページ巡回を行わない

## 出力先

CSVはスクリプトと同じ階層の `output/` フォルダに保存されます。
