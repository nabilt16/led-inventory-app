-- reset_stock_to_zero.sql
-- DO NOT RUN WITHOUT EXPLICIT APPROVAL. Production data operation.
-- Target project: "pergola-system" (ref gwiieqmawtudoxafnjeg) ONLY.
-- Sets quantity = 0 on every row of led_inventory and santaf_inventory.
-- Keeps all rows, order numbers, LED types, lengths, min_quantity, order_lines and
-- santaf_movements. Single atomic DO block: any failed check rolls everything back.
-- Before running, check that santaf_inventory really has 14 rows (edit the 14 below if not).

DO $$
DECLARE
  led_before     bigint;
  saf_before     bigint;
  saf_min_before bigint;
  led_updated    bigint;
  saf_updated    bigint;
  led_after      bigint;
  saf_after      bigint;
  saf_min_after  bigint;
  led_nonzero    bigint;
  saf_nonzero    bigint;
BEGIN
  IF to_regclass('public.led_inventory') IS NULL
     OR to_regclass('public.santaf_inventory') IS NULL THEN
    RAISE EXCEPTION 'ABORT: expected tables not found - wrong project?';
  END IF;

  IF (SELECT count(*) FROM information_schema.columns
      WHERE table_schema='public' AND table_name='led_inventory'
        AND column_name IN ('quantity','led_type','order_number')) <> 3
     OR (SELECT count(*) FROM information_schema.columns
      WHERE table_schema='public' AND table_name='santaf_inventory'
        AND column_name IN ('quantity','min_quantity','length')) <> 3 THEN
    RAISE EXCEPTION 'ABORT: expected columns not found - wrong project?';
  END IF;

  -- Refuse while standard Santaf is staged for an order (picked, not yet issued): resetting
  -- available stock would leave staged goods untracked. Issue or unpick them first.
  IF to_regclass('public.order_lines') IS NOT NULL AND EXISTS (
       SELECT 1 FROM public.order_lines WHERE picked_qty > 0 AND issued_on IS NULL) THEN
    RAISE EXCEPTION 'ABORT: there are staged (picked, not issued) Santaf lines; issue or unpick them first';
  END IF;

  LOCK TABLE public.led_inventory, public.santaf_inventory IN EXCLUSIVE MODE;

  SELECT count(*) INTO led_before FROM public.led_inventory;
  SELECT count(*), coalesce(sum(min_quantity),0)
    INTO saf_before, saf_min_before FROM public.santaf_inventory;

  IF saf_before <> 14 THEN
    RAISE EXCEPTION 'ABORT: santaf_inventory has % rows, expected 14', saf_before;
  END IF;

  UPDATE public.led_inventory    SET quantity = 0 WHERE true;
  GET DIAGNOSTICS led_updated = ROW_COUNT;
  UPDATE public.santaf_inventory SET quantity = 0 WHERE true;
  GET DIAGNOSTICS saf_updated = ROW_COUNT;

  SELECT count(*) FILTER (WHERE quantity IS DISTINCT FROM 0), count(*)
    INTO led_nonzero, led_after FROM public.led_inventory;
  SELECT count(*) FILTER (WHERE quantity IS DISTINCT FROM 0), count(*),
         coalesce(sum(min_quantity),0)
    INTO saf_nonzero, saf_after, saf_min_after FROM public.santaf_inventory;

  IF led_nonzero <> 0 OR saf_nonzero <> 0 THEN
    RAISE EXCEPTION 'ROLLBACK: non-zero quantities remain (led %, santaf %)', led_nonzero, saf_nonzero;
  END IF;
  IF led_updated <> led_before OR led_after <> led_before THEN
    RAISE EXCEPTION 'ROLLBACK: LED row count mismatch';
  END IF;
  IF saf_updated <> saf_before OR saf_after <> saf_before THEN
    RAISE EXCEPTION 'ROLLBACK: Santaf row count mismatch';
  END IF;
  IF saf_min_after <> saf_min_before THEN
    RAISE EXCEPTION 'ROLLBACK: min_quantity thresholds changed';
  END IF;

  RAISE NOTICE 'SUCCESS: led_inventory rows updated = %, santaf_inventory rows updated = %',
               led_updated, saf_updated;
END $$;
