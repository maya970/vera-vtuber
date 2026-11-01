import os
import sqlite3
import sys
import zipfile
from pathlib import Path

p = Path(__file__).resolve().parent.parent / "yousuu.sqlite"
print("size", os.path.getsize(p))
con = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
cur = con.cursor()
tabs = [r[0] for r in cur.execute("select name from sqlite_master where type='table' order by name")]
print("TABLES:", tabs)
for t in tabs:
    try:
        n = cur.execute(f'select count(*) from "{t}"').fetchone()[0]
        print(f"\n== {t} rows={n}")
        sql = cur.execute(f'select sql from sqlite_master where name="{t}"').fetchone()[0]
        print(sql[:1500])
        for row in cur.execute(f'select * from "{t}" limit 2'):
            print("  SAMPLE:", str(row)[:700])
    except Exception as e:
        print("ERR", t, e)

z = Path(__file__).resolve().parent.parent / "yousuu.zip"
if os.path.exists(z):
    with zipfile.ZipFile(z) as zf:
        print("\nZIP:", [(i.filename, i.file_size) for i in zf.infolist()][:30])
