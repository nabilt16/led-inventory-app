-- Local throwaway-Postgres test. ASSUMED legacy schema (real one not verified).
-- Fake data only. Run with: psql -v ON_ERROR_STOP=1 -f local_pg_test.sql
CREATE ROLE anon NOLOGIN;
GRANT USAGE ON SCHEMA public TO anon;

CREATE TABLE led_inventory (id bigserial PRIMARY KEY, order_number text, led_type text,
  quantity integer, notes text, created_at timestamptz DEFAULT now());
CREATE TABLE santaf_inventory (id bigserial PRIMARY KEY, length integer, quantity integer,
  min_quantity integer);
CREATE TABLE santaf_movements (id bigserial PRIMARY KEY, length integer, quantity integer,
  type text, date date);

INSERT INTO led_inventory (order_number, led_type, quantity, notes)
  VALUES ('S-1','LED 3 m',5,'a'), ('S-1','LED 1.5 m',2,NULL), ('S-2','LED 15 m',NULL,'x');
INSERT INTO santaf_inventory (length, quantity, min_quantity)
  SELECT l, (l/500), 20 + (l/1500) FROM unnest(ARRAY[1500,2000,2500,3000,3500,4000,4500,5000,5500,6000,6500,7000,7500,8000]) l;
INSERT INTO santaf_movements (length, quantity, type, date) VALUES (2000, 3, 'OUT', '2026-10-01');
CREATE TEMP TABLE before_snapshot AS
  SELECT (SELECT count(*) FROM led_inventory) led_rows,
         (SELECT count(*) FROM santaf_inventory) saf_rows,
         (SELECT sum(min_quantity) FROM santaf_inventory) min_sum,
         (SELECT count(*) FROM santaf_movements) mv_rows;

\echo == 1. migration applies
\i /work/migrations/001_order_lines.sql
\echo == 2. migration is idempotent (second run)
\i /work/migrations/001_order_lines.sql

\echo == 3. anon can insert/select/update, cannot delete, constraints enforced
SET ROLE anon;
INSERT INTO order_lines (customer_order_number, category, length_mm, quantity, item_spec)
  VALUES ('844122','SANTAF',2000,4,'x'), ('844122','SANTAF',3000,2,'x'), ('844122','LED',3000,1,'LED');
SELECT count(*) AS lines_for_844122 FROM order_lines WHERE customer_order_number='844122';
UPDATE order_lines SET coordination_date='2026-10-25' WHERE customer_order_number='844122';
DO $$ BEGIN
  BEGIN DELETE FROM order_lines; RAISE EXCEPTION 'anon DELETE was allowed';
  EXCEPTION WHEN insufficient_privilege THEN NULL; END;
END $$;
-- each bad insert must fail
DO $$ BEGIN
  BEGIN INSERT INTO order_lines (customer_order_number,category,length_mm,quantity) VALUES ('1','LED',100,0);
    RAISE EXCEPTION 'qty 0 accepted'; EXCEPTION WHEN check_violation THEN NULL; END;
  BEGIN INSERT INTO order_lines (customer_order_number,category,length_mm,quantity) VALUES ('  ','LED',100,1);
    RAISE EXCEPTION 'blank order accepted'; EXCEPTION WHEN check_violation THEN NULL; END;
  BEGIN INSERT INTO order_lines (customer_order_number,category,length_mm,quantity) VALUES ('1','XYZ',100,1);
    RAISE EXCEPTION 'bad category accepted'; EXCEPTION WHEN check_violation THEN NULL; END;
  BEGIN INSERT INTO order_lines (customer_order_number,category,length_mm,quantity,stock_applied)
        VALUES ('1','LED',100,1,true);
    RAISE EXCEPTION 'stock_applied without receipt accepted'; EXCEPTION WHEN check_violation THEN NULL; END;
END $$;
RESET ROLE;

\echo == 4. existing tables untouched by migration
SELECT (SELECT count(*) FROM led_inventory)=led_rows AS led_ok,
       (SELECT count(*) FROM santaf_inventory)=saf_rows AS saf_ok,
       (SELECT sum(min_quantity) FROM santaf_inventory)=min_sum AS min_ok,
       (SELECT sum(quantity) FROM led_inventory)=7 AS led_qty_untouched
FROM before_snapshot;

\echo == 5. reset script
\i /work/ops/reset_stock_to_zero.sql
SELECT (SELECT coalesce(sum(quantity),0) FROM led_inventory) AS led_total,
       (SELECT coalesce(sum(quantity),0) FROM santaf_inventory) AS santaf_total,
       (SELECT count(*) FROM led_inventory)=led_rows AS led_rows_kept,
       (SELECT count(*) FROM santaf_inventory)=saf_rows AS saf_rows_kept,
       (SELECT sum(min_quantity) FROM santaf_inventory)=min_sum AS min_kept,
       (SELECT count(*) FROM santaf_movements)=mv_rows AS movements_kept,
       (SELECT count(*) FROM order_lines)=3 AS order_lines_kept
FROM before_snapshot;

\echo == 6. reset aborts safely when a check fails (wrong row count)
DELETE FROM santaf_inventory WHERE length=8000;
UPDATE led_inventory SET quantity = 9;
\set ON_ERROR_STOP off
\i /work/ops/reset_stock_to_zero.sql
\set ON_ERROR_STOP on
SELECT sum(quantity) AS led_total_after_aborted_reset FROM led_inventory;  -- expect 27: nothing changed
