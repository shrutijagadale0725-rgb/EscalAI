import sqlite3
conn = sqlite3.connect("cases.db")
conn.execute("DELETE FROM case_index")
for t in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE '%checkpoint%'").fetchall():
    conn.execute(f"DELETE FROM {t[0]}")
conn.commit()
conn.close()
print("Cleared.")