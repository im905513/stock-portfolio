-- 002_record_001.sql
-- 001_seed_thesis.sql 在 schema_migrations 表建立之前就已經執行過了
-- 這筆記錄讓系統知道 001 已經跑過，避免重複執行
-- Idempotent: IF NOT EXISTS

INSERT OR IGNORE INTO schema_migrations (version) VALUES ('001_seed_thesis');
