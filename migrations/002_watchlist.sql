-- 002_watchlist.sql
-- 為 stocks 表加入 watch_status 欄位，區分「持有中」與「只追蹤」
-- active    = 已持有 / 已交易過 (預設，向下相容)
-- watchlist = 只追蹤、未持倉 (AI 挖到的候選)
-- archived  = 已退出追蹤
ALTER TABLE stocks ADD COLUMN watch_status TEXT NOT NULL DEFAULT 'active';
CREATE INDEX IF NOT EXISTS idx_stocks_watch_status ON stocks(watch_status);
