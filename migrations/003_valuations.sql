-- 三值估價快取表
CREATE TABLE IF NOT EXISTS valuations (
  stock_id        INTEGER PRIMARY KEY,
  symbol          TEXT NOT NULL,
  method          TEXT NOT NULL,          -- 'pe' | 'yield'
  category        TEXT,                   -- '低估成長股'|'金融股'|'景氣循環股'|'ETF'
  eps_used        REAL,                   -- forward EPS or TTM
  eps_growth_ytd  REAL,                   -- 近 4 季 YoY
  avg_dividend    REAL,
  pe_low          REAL,
  pe_mid          REAL,
  pe_high         REAL,
  cheap_price     REAL,
  fair_price      REAL,
  expensive_price REAL,
  current_price   REAL,
  tag             TEXT,                   -- 'cheap'|'fair'|'expensive'
  source          TEXT DEFAULT 'self',    -- 'self' | 'finmind_sponsor' (Phase 2)
  computed_at     TEXT NOT NULL,
  FOREIGN KEY (stock_id) REFERENCES stocks(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_valuations_tag ON valuations(tag);
CREATE INDEX IF NOT EXISTS idx_valuations_category ON valuations(category);
CREATE INDEX IF NOT EXISTS idx_valuations_symbol ON valuations(symbol);
