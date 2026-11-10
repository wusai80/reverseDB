package research.alfio;

import alfio.util.Json;
import java.sql.*;
import java.util.*;
import org.springframework.jdbc.core.ConnectionCallback;
import org.springframework.jdbc.core.namedparam.*;

/** Experimental boundary only. Activation requires the pinned, isolated test deployment. */
public final class AlfioOrderedBatch {
  public static final String ENABLE = "reversedb.alfio.orderedBatch";
  public static final String SQL = "update ticket set tickets_reservation_id = :reservationId, status = 'PENDING', category_id = :categoryId, user_language = :userLanguage, src_price_cts = :srcPriceCts, currency_code = :currencyCode, metadata = :ticketMetadata::jsonb, vat_status = :vatStatus::VAT_STATUS, first_name = :firstName, last_name = :lastName, email_address = :email, full_name = :fullName where id = :id";
  static final String[] NAMES = {"reservationId","categoryId","userLanguage","srcPriceCts","currencyCode","ticketMetadata","vatStatus","firstName","lastName","email","fullName","id"};
  static final List<Map<String,Object>> EVENTS = Collections.synchronizedList(new ArrayList<>());
  static void event(String path,int size) { EVENTS.add(Map.of("path",path,"rows",size)); }
  public static List<Map<String,Object>> snapshot() { synchronized(EVENTS) { return List.copyOf(EVENTS); } }
  public static int[] execute(NamedParameterJdbcTemplate template,String sql,SqlParameterSource[] params) {
    String reason=null;
    if(!Boolean.getBoolean(ENABLE))reason="disabled";
    else if(template.getClass()!=NamedParameterJdbcTemplate.class)reason="custom-template";
    else if(!SQL.equals(sql))reason="unknown-sql";
    else if(params.length==0)reason="empty";
    List<List<Object>> rows=new ArrayList<>();
    if(reason==null) {
      for(SqlParameterSource param:params) {
        if(param==null || param.getClass()!=MapSqlParameterSource.class || !new HashSet<>(Arrays.asList(param.getParameterNames())).equals(Set.of(NAMES))) { reason="unknown-parameter-shape";break; }
        List<Object> row=new ArrayList<>();
        for(int i=0;i<NAMES.length;i++) {
          String name=NAMES[i];Object value=param.getValue(name);
          Class<?> expected=(i==1||i==3||i==11)?Integer.class:String.class;
          if(param.getSqlType(name)!=SqlParameterSource.TYPE_UNKNOWN || param.getTypeName(name)!=null || (value!=null&&value.getClass()!=expected)) { reason="unknown-binding";break; }
          row.add(value);
        }
        if(reason!=null)break;
        rows.add(row);
      }
    }
    if(reason!=null) { event("fallback:"+reason,params.length);return template.batchUpdate(sql,params); }
    // Original Java preparation already completed. Transport carries every value, including NULL.
    String payload=Json.toJson(rows);
    int[] result=template.getJdbcTemplate().execute((ConnectionCallback<int[]>)connection->{
      if(connection.getAutoCommit())return null; // no statement has executed; decide before writing
      event("candidate-attempt",params.length);
      try(PreparedStatement statement=connection.prepareStatement("select rdb_experiment.reserve_ticket_batch(?::jsonb)")) {
        statement.setString(1,payload);
        try(ResultSet rs=statement.executeQuery()) {
          if(!rs.next())throw new SQLException("Missing ordered batch result");
          java.sql.Array array=rs.getArray(1);
          try {
            Object[] counts=(Object[])array.getArray();int[] values=new int[counts.length];
            for(int i=0;i<values.length;i++)values[i]=((Number)counts[i]).intValue();
            return values;
          } finally { array.free(); }
        }
      }
    });
    if(result==null) { event("fallback:autocommit",params.length);return template.batchUpdate(sql,params); }
    event("candidate-return",params.length);
    return result;
    // Deliberately no catch/retry: database errors propagate through the same Spring translator.
  }
}
