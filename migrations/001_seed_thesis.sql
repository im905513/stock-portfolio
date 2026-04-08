-- 001_seed_thesis.sql
-- 為 5 檔持股寫入初始 thesis (target / stop loss / exit condition)
-- 生成於 2026-04-08, AI 基於當時 PE/PBR/殖利率/52週區間/z-score/外資動向 擬定
-- 執行邏輯：只在該檔 active thesis 尚未存在時才 INSERT (idempotent)

-- 1229 聯華 (食品 · 存股)
INSERT INTO thesis (stock_id, thesis, exit_condition, target_price, stop_loss, status)
SELECT s.id,
       '食品民生剛需,高殖利率 4.44%,股價在 52 週低位區 (z-score -0.84),外資近 5 日淨買超 +1.1M,長線存股核心',
       '殖利率跌破 3.5% 或月營收連 3 季衰退',
       52, 41, 'active'
FROM stocks s
WHERE s.symbol='1229'
  AND NOT EXISTS (SELECT 1 FROM thesis t WHERE t.stock_id=s.id AND t.status='active');

-- 2330 台積電 (半導體 · 題材)
INSERT INTO thesis (stock_id, thesis, exit_condition, target_price, stop_loss, status)
SELECT s.id,
       'AI/HPC 龍頭,CoWoS 產能滿載到 2027,先進製程獨家,PE 28 位於合理區間',
       'PE > 35 或 CoWoS 訂單明顯減少',
       2300, 1700, 'active'
FROM stocks s
WHERE s.symbol='2330'
  AND NOT EXISTS (SELECT 1 FROM thesis t WHERE t.stock_id=s.id AND t.status='active');

-- 2883 凱基金 (金融 · 存股)
INSERT INTO thesis (stock_id, thesis, exit_condition, target_price, stop_loss, status)
SELECT s.id,
       '壽險轉型 + 升息受惠,外資 5 日淨買超 +7.5M 強勁,PBR 1.16 仍低於同業均值,殖利率 4.62%',
       '殖利率跌破 4% 或 PBR > 1.5',
       24, 18, 'active'
FROM stocks s
WHERE s.symbol='2883'
  AND NOT EXISTS (SELECT 1 FROM thesis t WHERE t.stock_id=s.id AND t.status='active');

-- 2891 中信金 (金融 · 存股, 相對謹慎)
INSERT INTO thesis (stock_id, thesis, exit_condition, target_price, stop_loss, status)
SELECT s.id,
       '金控龍頭,但 PBR 2.17 偏貴、z-score +2.56 近年高、外資 5 日淨賣 -1.7M,需相對謹慎,以緊停損保護',
       'PBR > 2.5 或外資連續 2 週淨賣',
       62, 50, 'active'
FROM stocks s
WHERE s.symbol='2891'
  AND NOT EXISTS (SELECT 1 FROM thesis t WHERE t.stock_id=s.id AND t.status='active');

-- GDX 金礦股 ETF (原物料 · 題材)
INSERT INTO thesis (stock_id, thesis, exit_condition, target_price, stop_loss, status)
SELECT s.id,
       '金礦股 ETF,題材帳戶操作,對沖通膨 / 地緣風險,成本 84.43 已 +12%,題材仍在',
       '金價突破後回落 10% 或美元 DXY > 108',
       110, 85, 'active'
FROM stocks s
WHERE s.symbol='GDX'
  AND NOT EXISTS (SELECT 1 FROM thesis t WHERE t.stock_id=s.id AND t.status='active');
