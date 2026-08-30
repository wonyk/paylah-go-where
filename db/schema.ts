import { index, integer, primaryKey, sqliteTable, text } from "drizzle-orm/sqlite-core";

export const settings = sqliteTable("settings", {
  key: text("key").primaryKey(),
  value: text("value").notNull(),
});

export const snapshots = sqliteTable("snapshots", {
  id: text("id").primaryKey(),
  pdfHash: text("pdf_hash").notNull().unique(),
  sourceUrl: text("source_url"),
  pdfBytes: integer("pdf_bytes").notNull(),
  recordCount: integer("record_count").notNull().default(0),
  status: text("status", { enum: ["staging", "active", "archived"] }).notNull(),
  createdAt: integer("created_at").notNull(),
});

export const merchants = sqliteTable("merchants", {
  snapshotId: text("snapshot_id").notNull().references(() => snapshots.id, { onDelete: "cascade" }),
  seq: integer("seq").notNull(),
  name: text("name").notNull(),
  address: text("address").notNull().default(""),
  unit: text("unit").notNull().default(""),
  postalCode: text("postal_code").notNull(),
  category: text("category").notNull(),
  kind: text("kind").notNull().default("merchant"),
  venue: text("venue").notNull().default(""),
}, (table) => [
  primaryKey({ columns: [table.snapshotId, table.seq] }),
  index("idx_merchants_snapshot_postal").on(table.snapshotId, table.postalCode),
  index("idx_merchants_snapshot_name").on(table.snapshotId, table.name),
  index("idx_merchants_snapshot_address").on(table.snapshotId, table.address),
]);
