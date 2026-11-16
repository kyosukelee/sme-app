#!/bin/sh
# 開発用DBの初期化(DBを初めて作るときだけ実行される)
# db/migrations の SQL を番号順に流してから、開発用データを入れる
set -e
for f in /db/migrations/*.sql /db/dev/*.sql; do
  echo "実行: $f"
  psql --no-psqlrc -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" -q -f "$f"
done
