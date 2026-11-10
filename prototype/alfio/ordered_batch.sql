-- Installed only in disposable experiment databases, owned/executed by alfio_user.
CREATE SCHEMA rdb_experiment AUTHORIZATION alfio_user;
REVOKE ALL ON SCHEMA rdb_experiment FROM PUBLIC;
CREATE FUNCTION rdb_experiment.reserve_ticket_batch(parameters jsonb)
RETURNS integer[]
LANGUAGE plpgsql VOLATILE SECURITY INVOKER
SET search_path = pg_catalog, public
AS $$
DECLARE
  p jsonb;
  affected integer;
  counts integer[] := ARRAY[]::integer[];
BEGIN
  -- Keep each UPDATE and its casts/constraints in the original row order.
  -- Do not use a set UPDATE, deduplicate IDs, or prevalidate all database values.
  FOR p IN SELECT value FROM jsonb_array_elements(parameters) WITH ORDINALITY AS r(value, ordinal) ORDER BY ordinal
  LOOP
    UPDATE public.ticket SET
      tickets_reservation_id = p->>0,
      status = 'PENDING',
      category_id = (p->>1)::integer,
      user_language = p->>2,
      src_price_cts = (p->>3)::integer,
      currency_code = p->>4,
      metadata = (p->>5)::jsonb,
      vat_status = (p->>6)::public.vat_status,
      first_name = p->>7,
      last_name = p->>8,
      email_address = p->>9,
      full_name = p->>10
    WHERE id = (p->>11)::integer;
    GET DIAGNOSTICS affected = ROW_COUNT;
    counts := array_append(counts, affected);
  END LOOP;
  RETURN counts;
END
$$;
REVOKE ALL ON FUNCTION rdb_experiment.reserve_ticket_batch(jsonb) FROM PUBLIC;
