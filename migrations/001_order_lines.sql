-- 001_order_lines.sql
-- ADDITIVE ONLY: creates one new table. Does not touch led_inventory,
-- santaf_inventory or santaf_movements. Safe to run twice (IF NOT EXISTS).
-- Run manually in the Supabase SQL Editor of project "pergola-system" ONLY.

CREATE TABLE IF NOT EXISTS public.order_lines (
  id                    uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  customer_order_number text        NOT NULL CHECK (length(btrim(customer_order_number)) > 0),
  category              text        NOT NULL CHECK (category IN ('LED', 'SANTAF')),
  supplier_order_ref    text,
  supplier_order_date   date,
  ordered_by            text,
  item_spec             text,
  length_mm             integer     NOT NULL CHECK (length_mm > 0),
  quantity              integer     NOT NULL CHECK (quantity > 0),
  status                text        NOT NULL DEFAULT 'ordered'
                                    CHECK (status IN ('ordered', 'received', 'cancelled')),
  stock_applied         boolean     NOT NULL DEFAULT false,
  coordination_date     date,
  received_date         date,
  received_qty          integer     CHECK (received_qty IS NULL OR received_qty > 0),
  storage_location      text,
  notes                 text,
  created_at            timestamptz NOT NULL DEFAULT now(),
  updated_at            timestamptz NOT NULL DEFAULT now(),
  -- stock can only be applied by a confirmed receipt
  CONSTRAINT order_lines_stock_requires_receipt
    CHECK (stock_applied = false OR (status = 'received' AND received_qty IS NOT NULL))
);

CREATE INDEX IF NOT EXISTS order_lines_order_idx  ON public.order_lines (customer_order_number);
CREATE INDEX IF NOT EXISTS order_lines_coord_idx  ON public.order_lines (coordination_date);
CREATE INDEX IF NOT EXISTS order_lines_status_idx ON public.order_lines (status);

-- Row-level security: the app has no user login and uses the anon key, so the
-- anon role needs SELECT/INSERT/UPDATE. DELETE is intentionally NOT granted.
ALTER TABLE public.order_lines ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS order_lines_anon_select ON public.order_lines;
DROP POLICY IF EXISTS order_lines_anon_insert ON public.order_lines;
DROP POLICY IF EXISTS order_lines_anon_update ON public.order_lines;
CREATE POLICY order_lines_anon_select ON public.order_lines FOR SELECT TO anon USING (true);
CREATE POLICY order_lines_anon_insert ON public.order_lines FOR INSERT TO anon WITH CHECK (true);
CREATE POLICY order_lines_anon_update ON public.order_lines FOR UPDATE TO anon USING (true) WITH CHECK (true);

GRANT SELECT, INSERT, UPDATE ON public.order_lines TO anon;
