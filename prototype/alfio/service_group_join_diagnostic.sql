-- Diagnostic only: not installed, not an admitted lowering. No ORDER BY or
-- exception/identity reconstruction is claimed by this raw relational shape.
SELECT to_jsonb(i) AS item_row, to_jsonb(s) AS definition_row
FROM additional_service_item i
LEFT JOIN additional_service_with_currency s
  ON s.id = i.additional_service_id_fk AND s.event_id_fk = i.event_id_fk
WHERE i.event_id_fk = :eventId AND i.tickets_reservation_uuid = :reservationId
