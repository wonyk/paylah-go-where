CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS snapshots (
  id TEXT PRIMARY KEY, pdf_hash TEXT NOT NULL UNIQUE, source_url TEXT,
  pdf_bytes INTEGER NOT NULL, record_count INTEGER NOT NULL DEFAULT 0,
  status TEXT NOT NULL CHECK (status IN ('staging', 'active', 'archived')),
  created_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS merchants (
  snapshot_id TEXT NOT NULL, seq INTEGER NOT NULL, name TEXT NOT NULL,
  address TEXT NOT NULL DEFAULT '', unit TEXT NOT NULL DEFAULT '', postal_code TEXT NOT NULL,
  category TEXT NOT NULL, kind TEXT NOT NULL DEFAULT 'merchant', venue TEXT NOT NULL DEFAULT '',
  PRIMARY KEY (snapshot_id, seq),
  FOREIGN KEY (snapshot_id) REFERENCES snapshots(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_merchants_snapshot_postal ON merchants(snapshot_id, postal_code);
CREATE INDEX IF NOT EXISTS idx_merchants_snapshot_name ON merchants(snapshot_id, name);
CREATE INDEX IF NOT EXISTS idx_merchants_snapshot_address ON merchants(snapshot_id, address);
INSERT OR IGNORE INTO settings(key, value) VALUES (
  'source_url',
  'https://www.dbs.com.sg/iwov-resources/media/pdf/deposits/promotions/paylah/saturdays/dbs-paylah-saturdays-participating-merchants.pdf'
);
