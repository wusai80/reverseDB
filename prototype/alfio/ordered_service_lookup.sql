-- Experimental helper only. Original Java item read/grouping determines p_ids.
-- No installation into the application; SECURITY INVOKER, no isolation change.
CREATE SCHEMA IF NOT EXISTS reversedb_probe;
CREATE OR REPLACE FUNCTION reversedb_probe.ordered_service_lookup(p_event integer, p_ids integer[])
RETURNS jsonb LANGUAGE plpgsql VOLATILE SECURITY INVOKER AS $$
DECLARE
    key integer;
    step integer := 0;
    rows jsonb;
    records jsonb := '[]'::jsonb;
BEGIN
    FOREACH key IN ARRAY p_ids LOOP
        step := step + 1;
        -- TEST_BARRIER_BEFORE_LOOKUP
        SELECT coalesce(jsonb_agg(to_jsonb(s)), '[]'::jsonb) INTO rows
        FROM public.additional_service_with_currency s
        WHERE s.id = key AND s.event_id_fk = p_event;
        IF jsonb_array_length(rows) <> 1 THEN
            -- A client-side cardinality error must not abort the SQL transaction.
            -- Return a stop marker; the host must reconstruct the original error.
            RETURN jsonb_build_object('status','cardinality_error','actual',jsonb_array_length(rows),
                'failed_key',key,'attempts',step,'records',records);
        END IF;
        records := records || jsonb_build_array(jsonb_build_object('key',key,'definition',rows->0));
    END LOOP;
    RETURN jsonb_build_object('status','ok','attempts',step,'records',records);
END
$$;
REVOKE ALL ON FUNCTION reversedb_probe.ordered_service_lookup(integer,integer[]) FROM PUBLIC;
GRANT USAGE ON SCHEMA reversedb_probe TO alfio_user, probe_items_only;
GRANT EXECUTE ON FUNCTION reversedb_probe.ordered_service_lookup(integer,integer[]) TO alfio_user, probe_items_only;
