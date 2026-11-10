package research;

import java.lang.reflect.*;
import java.math.BigDecimal;
import java.nio.file.*;
import java.sql.*;
import java.util.*;
import org.json.*;

/** Frozen adapter and helpers, strict supplied JDBC responses; no SQL engine. */
public final class CallerObservationProbe {
  static Object proxy(Class<?> type,InvocationHandler handler) { return Proxy.newProxyInstance(CallerObservationProbe.class.getClassLoader(),new Class<?>[]{type},handler); }
  static Object object(Object p,Method m,Object[] a) { return switch(m.getName()) { case "toString" -> "CallerBoundary";case "hashCode" -> System.identityHashCode(p);case "equals" -> p==a[0];default -> throw new UnsupportedOperationException(m.toString()); }; }
  static final class Boundary {
    final String variant;final JSONArray events=new JSONArray();
    Boundary(String variant) { this.variant=variant; }
    Connection connection() {
      return (Connection)proxy(Connection.class,(p,m,a)-> {
        if(m.getDeclaringClass()==Object.class)return object(p,m,a);
        if(!m.getName().equals("prepareStatement"))throw new UnsupportedOperationException(m.toString());
        String sql=((String)a[0]).replaceAll("\\s+"," ").trim().toLowerCase(Locale.ROOT);Map<Integer,Object> bindings=new LinkedHashMap<>();
        return proxy(PreparedStatement.class,(s,n,b)-> {
          if(n.getDeclaringClass()==Object.class)return object(s,n,b);
          if(n.getName().equals("close"))return null;
          if(Set.of("setInt","setObject").contains(n.getName())) { bindings.put((Integer)b[0],b[1]);return null; }
          if(n.getName().equals("executeQuery")) {
            events.put(new JSONObject().put("op","executeQuery").put("sql",sql).put("bindings",new JSONObject(bindings)));
            return rows(response(sql,bindings),sql);
          }
          throw new UnsupportedOperationException(n.toString());
        });
      });
    }
    Map<String,Object> response(String sql,Map<Integer,Object> bindings) throws SQLException {
      Map<String,Object> row=new LinkedHashMap<>();
      if(sql.startsWith("select c_first,")) {
        for(String k:List.of("c_first","c_middle","c_last","c_street_1","c_street_2","c_city","c_state","c_zip","c_phone"))row.put(k,"x");
        row.put("c_id",1);row.put("c_credit","GC");row.put("c_credit_lim",1000);row.put("c_discount",0.1);row.put("c_balance",10);
        row.put("c_ytd_payment",10);row.put("c_payment_cnt",1);row.put("c_since",new Timestamp(1762300800000L));return row;
      }
      if(sql.startsWith("select o_id,")) {
        row.put("o_id",variant.equals("id_changed")?202:101);row.put("o_carrier_id",variant.equals("carrier_changed")?99:3);
        row.put("o_entry_d",new Timestamp(variant.equals("time_changed")?1L:1762300800101L));return row;
      }
      if(sql.startsWith("select ol_i_id,")) {
        if(variant.equals("display_failure"))throw new SQLException("injected display query failure","58000");
        row.put("ol_i_id",variant.equals("display_values_changed")?999:10);row.put("ol_supply_w_id",1);row.put("ol_quantity",2);
        row.put("ol_amount",5.25);row.put("ol_delivery_d",new Timestamp(1762300800000L));return row;
      }
      if(sql.startsWith("select * from oorder ") || sql.startsWith("select * from order_line ")) {
        int id=((Number)bindings.get(2)).intValue();if(id!=101 && id!=202)throw new AssertionError("unexpected query id "+id);
        if(sql.startsWith("select * from oorder ")) {
          row.put("o_id",id);row.put("o_carrier_id",id==101?3:4);row.put("o_entry_d",new Timestamp(1762300800000L+id));
        } else { row.put("ol_o_id",id);row.put("ol_number",1);row.put("ol_i_id",10);row.put("ol_amount",5.25); }
        return row;
      }
      throw new UnsupportedOperationException(sql);
    }
    ResultSet rows(Map<String,Object> row,String sql) {
      List<String> columns=new ArrayList<>(row.keySet());int[] next={0};boolean[] wasNull={false};
      ResultSetMetaData metadata=(ResultSetMetaData)proxy(ResultSetMetaData.class,(p,m,a)->switch(m.getName()) {
        case "getColumnCount" -> columns.size();case "getColumnLabel","getColumnName" -> columns.get((Integer)a[0]-1);default -> throw new UnsupportedOperationException(m.toString()); });
      return (ResultSet)proxy(ResultSet.class,(p,m,a)-> {
        if(m.getDeclaringClass()==Object.class)return object(p,m,a);
        String op=m.getName();if(op.equals("close"))return null;if(op.equals("next"))return next[0]++==0;
        if(op.equals("getMetaData"))return metadata;if(op.equals("wasNull"))return wasNull[0];
        if(next[0]!=1 || a==null || a.length!=1)throw new UnsupportedOperationException(m.toString());
        String key=a[0] instanceof Integer i?columns.get(i-1):a[0].toString().toLowerCase(Locale.ROOT);
        if(!row.containsKey(key))throw new AssertionError("unprovided column "+key);Object v=row.get(key);wasNull[0]=v==null;
        events.put(new JSONObject().put("op",op).put("sql",sql).put("column",key).put("value",v==null?JSONObject.NULL:v.toString()));
        return switch(op) {
          case "getObject","getTimestamp" -> v;case "getString" -> v==null?null:v.toString();case "getInt" -> ((Number)v).intValue();
          case "getLong" -> ((Number)v).longValue();case "getFloat" -> ((Number)v).floatValue();case "getDouble" -> ((Number)v).doubleValue();
          case "getBigDecimal" -> new BigDecimal(v.toString());default -> throw new UnsupportedOperationException(m.toString());
        };
      });
    }
  }
  public static void main(String[] args) throws Exception {
    Class<?> adapter=Class.forName("com.oltpbenchmark.api.TpccApp");Method entry=adapter.getDeclaredMethod("reference",Connection.class,JSONObject.class);entry.setAccessible(true);
    JSONArray runs=new JSONArray();
    for(String variant:List.of("base","id_changed","carrier_changed","time_changed","display_values_changed","display_failure")) {
      Boundary b=new Boundary(variant);JSONObject r=new JSONObject().put("variant",variant);
      try { r.put("response",entry.invoke(null,b.connection(),new JSONObject().put("type","OrderStatus").put("district",1).put("customer",1))).put("status","returned"); }
      catch(InvocationTargetException error) {
        if(!(error.getCause() instanceof SQLException sql) || !variant.equals("display_failure") || !"58000".equals(sql.getSQLState()))throw error;
        r.put("status","error").put("sql_state",sql.getSQLState());
      }
      runs.put(r.put("events",b.events));
    }
    Files.writeString(Path.of(args[0]),new JSONObject().put("runs",runs)
      .put("adapter_origin",adapter.getProtectionDomain().getCodeSource().getLocation().toString())
      .put("helper_origin",Class.forName("com.oltpbenchmark.benchmarks.tpcc.procedures.OrderStatus").getProtectionDomain().getCodeSource().getLocation().toString())
      .put("scope","unchanged business response builder with supplied JDBC replies; no SQL evaluation or AppRuntime transaction/status wrapper").toString(2)+"\n");
  }
}
