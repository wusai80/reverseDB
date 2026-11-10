-- Deliberate branch fixture, not the standard initial TPC-C state. Applied identically to reference and candidate.
DELETE FROM new_order WHERE NOT (no_w_id=1 AND no_d_id=1 AND no_o_id IN (2101,2102));
