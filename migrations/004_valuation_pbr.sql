-- 擴充 valuations 表：加入股淨比法欄位
ALTER TABLE valuations ADD COLUMN bps REAL;
ALTER TABLE valuations ADD COLUMN pbr_low REAL;
ALTER TABLE valuations ADD COLUMN pbr_mid REAL;
ALTER TABLE valuations ADD COLUMN pbr_high REAL;
ALTER TABLE valuations ADD COLUMN pbr_cheap_price REAL;
ALTER TABLE valuations ADD COLUMN pbr_fair_price REAL;
ALTER TABLE valuations ADD COLUMN pbr_expensive_price REAL;
ALTER TABLE valuations ADD COLUMN pbr_tag TEXT;
