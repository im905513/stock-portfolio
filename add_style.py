import sqlite3, os
_BASE = os.path.dirname(os.path.abspath(__file__))
conn = sqlite3.connect(os.getenv("DB_PATH", os.path.join(_BASE, "stock.db")))
cur = conn.cursor()

# Add investment_style column
try:
    cur.execute("ALTER TABLE stocks ADD COLUMN investment_style TEXT DEFAULT 'dca'")
except Exception as e:
    print(f"Column may exist: {e}")

# Update existing stocks
style_map = {
    1229: 'dca',      # 聯華 - 食品存股
    2330: 'dca',      # 台積電 - 半導體 DCA
    2883: 'dca',      # 凱基金 - 金融 DCA
    2891: 'dca',      # 中信金 - 金融 DCA
    9999: 'thematic', # GDX - 主題/伊朗題材
}
for stock_id, style in style_map.items():
    cur.execute("UPDATE stocks SET investment_style=? WHERE id=?", (style, stock_id))

conn.commit()

# Verify
cur.execute("SELECT id, symbol, name, investment_style FROM stocks")
for r in cur.fetchall():
    print(r)
