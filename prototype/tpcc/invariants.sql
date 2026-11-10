-- Narrow functional consistency checks, not full TPC-C certification.
SELECT jsonb_build_object(
  'warehouse_district_ytd_mismatches',
    (SELECT count(*) FROM warehouse w WHERE w.w_ytd <>
      (SELECT sum(d.d_ytd) FROM district d WHERE d.d_w_id=w.w_id)),
  'district_next_order_mismatches',
    (SELECT count(*) FROM district d WHERE d.d_next_o_id <>
      (SELECT coalesce(max(o.o_id),0)+1 FROM oorder o WHERE o.o_w_id=d.d_w_id AND o.o_d_id=d.d_id)),
  'order_line_count_mismatches',
    (SELECT count(*) FROM oorder o LEFT JOIN
      (SELECT ol_w_id,ol_d_id,ol_o_id,count(*) n FROM order_line GROUP BY ol_w_id,ol_d_id,ol_o_id) l
      ON (l.ol_w_id,l.ol_d_id,l.ol_o_id)=(o.o_w_id,o.o_d_id,o.o_id)
      WHERE o.o_ol_cnt <> coalesce(l.n,0)),
  'new_order_without_order',
    (SELECT count(*) FROM new_order n LEFT JOIN oorder o
      ON (o.o_w_id,o.o_d_id,o.o_id)=(n.no_w_id,n.no_d_id,n.no_o_id) WHERE o.o_id IS NULL),
  'new_order_gaps',
    (SELECT count(*) FROM
      (SELECT no_w_id,no_d_id FROM new_order GROUP BY no_w_id,no_d_id
       HAVING count(*)<>max(no_o_id)-min(no_o_id)+1) gaps)
)::text;
