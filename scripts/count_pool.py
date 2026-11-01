import sqlite3

c = sqlite3.connect("file:yousuu.sqlite?mode=ro", uri=True)
q = lambda sql: c.execute(sql).fetchone()[0]
print("score>0 & raters>=20:", q("select count(*) from books where score>0 and scorerCount>=20"))
print("  tags not empty:", q("select count(*) from books where score>0 and scorerCount>=20 and tags is not null and tags<>''"))
print("  author not _出版:", q("select count(*) from books where score>0 and scorerCount>=20 and author not like '%_出版'"))
print("score>=5 & raters>=20:", q("select count(*) from books where score>=5 and scorerCount>=20"))
print("score>=6 & raters>=20:", q("select count(*) from books where score>=6 and scorerCount>=20"))
print("score>=6.5 & raters>=20:", q("select count(*) from books where score>=6.5 and scorerCount>=20"))
