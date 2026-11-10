package research;

import java.lang.reflect.*;
import java.math.*;
import java.nio.file.*;
import java.sql.*;
import java.util.*;
import org.json.*;
import reversedb.tpcc.AppRuntime;

/** Actual frozen adapter/Payment execution against a strict JDBC boundary double.
 * Captures source bindings and branch choices; does not emulate PostgreSQL.
 */
public class PaymentSemanticsProbe {
  static void require(boolean b,String m) { if(!b) throw new AssertionError(m); }
  static Object proxy(Class<?> type,InvocationHandler h) {
    return Proxy.newProxyInstance(PaymentSemanticsProbe.class.getClassLoader(),new Class<?>[]{type},h);
  }
  static Object objectMethod(Object p,Method m,Object[] a) {
    return switch(m.getName()) { case "toString" -> "PaymentBoundaryDouble";
      case "hashCode" -> System.identityHashCode(p); case "equals" -> p==a[0];
      default -> throw new UnsupportedOperationException(m.toString()); };
  }
  static final class Boundary {
    final Map<String,Object> customer=new LinkedHashMap<>();
    final JSONArray events=new JSONArray();
    String warehouseName="warehouse", districtName="district";
    Map<Integer,Object> balanceBindings,historyBindings;
    String storedCdata;
    Boundary(String credit,String balance,String data) {
      for(String k:List.of("c_first","c_middle","c_last","c_street_1","c_street_2","c_city","c_state","c_zip","c_phone")) customer.put(k,"x");
      customer.put("c_id",1); customer.put("c_d_id",1); customer.put("c_w_id",1);
      customer.put("c_credit",credit); customer.put("c_balance",new BigDecimal(balance));
      customer.put("c_ytd_payment",new BigDecimal("10.00")); customer.put("c_payment_cnt",1);
      customer.put("c_credit_lim",1000); customer.put("c_discount",0.1f);
      customer.put("c_since",Timestamp.valueOf("2026-01-01 00:00:00")); customer.put("c_data",data);
    }
    Connection connection() {
      return (Connection)proxy(Connection.class,(p,m,a)-> {
        if(m.getDeclaringClass()==Object.class) return objectMethod(p,m,a);
        if(m.getName().equals("prepareStatement")) return statement((String)a[0]);
        throw new UnsupportedOperationException(m.toString());
      });
    }
    PreparedStatement statement(String raw) {
      String sql=raw.replaceAll("\\s+"," ").trim().toLowerCase(Locale.ROOT);
      Map<Integer,Object> bindings=new LinkedHashMap<>();
      return (PreparedStatement)proxy(PreparedStatement.class,(p,m,a)-> {
        if(m.getDeclaringClass()==Object.class) return objectMethod(p,m,a);
        if(m.getName().equals("close")) return null;
        if(Set.of("setInt","setDouble","setBigDecimal","setString","setTimestamp","setObject").contains(m.getName())) {
          bindings.put((Integer)a[0],a[1]); return null;
        }
        if(m.getName().equals("executeUpdate")) {
          events.put(new JSONObject().put("sql",sql).put("operation","write"));
          if(sql.startsWith("update customer ")) {
            balanceBindings=new LinkedHashMap<>(bindings);
            // Return projection exposes the captured binding, without claiming
            // to emulate a numeric column's storage coercion or a transaction.
            customer.put("c_balance",bindings.get(1)); customer.put("c_ytd_payment",bindings.get(2));
            customer.put("c_payment_cnt",bindings.get(3));
            if(sql.contains("c_data = ?")) storedCdata=(String)bindings.get(4);
          } else if(sql.startsWith("insert into history ")) historyBindings=new LinkedHashMap<>(bindings);
          else if(!sql.startsWith("update warehouse ") && !sql.startsWith("update district ")) throw new AssertionError(sql);
          return 1;
        }
        if(m.getName().equals("executeQuery")) {
          events.put(new JSONObject().put("sql",sql).put("operation","read"));
          Map<String,Object> row=new LinkedHashMap<>();
          String[] columns=sql.substring(7,sql.indexOf(" from ")).split("\\s*,\\s*");
          for(String column:columns) {
            if(sql.contains(" from customer ")) {
              require(customer.containsKey(column),"Unknown fixture column "+column); row.put(column,customer.get(column));
            } else if(sql.contains(" from warehouse ")) row.put(column,column.equals("w_name")?warehouseName:"x");
            else if(sql.contains(" from district ")) row.put(column,column.equals("d_name")?districtName:"x");
            else throw new AssertionError(sql);
          }
          return rows(row);
        }
        throw new UnsupportedOperationException(m.toString());
      });
    }
    ResultSet rows(Map<String,Object> row) {
      List<String> columns=new ArrayList<>(row.keySet()); int[] next={0};
      ResultSetMetaData metadata=(ResultSetMetaData)proxy(ResultSetMetaData.class,(p,m,a)->switch(m.getName()) {
        case "getColumnCount" -> columns.size();
        case "getColumnLabel","getColumnName" -> columns.get((Integer)a[0]-1);
        default -> throw new UnsupportedOperationException(m.toString());
      });
      return (ResultSet)proxy(ResultSet.class,(p,m,a)-> {
        if(m.getDeclaringClass()==Object.class) return objectMethod(p,m,a);
        if(m.getName().equals("next")) return next[0]++==0;
        if(m.getName().equals("close")) return null;
        if(m.getName().equals("getMetaData")) return metadata;
        if(a==null || a.length!=1) throw new UnsupportedOperationException(m.toString());
        String key=a[0] instanceof Integer i ? columns.get(i-1):a[0].toString().toLowerCase(Locale.ROOT);
        require(row.containsKey(key),"Unknown fixture field "+key); Object value=row.get(key);
        return switch(m.getName()) {
          case "getObject","getTimestamp" -> value;
          case "getString" -> value==null?null:value.toString();
          case "getFloat" -> ((Number)value).floatValue();
          case "getDouble" -> ((Number)value).doubleValue();
          case "getInt" -> ((Number)value).intValue();
          default -> throw new UnsupportedOperationException(m.toString());
        };
      });
    }
  }
  static JSONObject command(String id,String amount) {
    return new JSONObject().put("id",id).put("type","Payment").put("terminal",1).put("warehouse",1)
      .put("district",1).put("customer_district",1).put("customer",1)
      .put("clock_ms",1762300800000L).put("amount",new BigDecimal(amount));
  }
  static void execute(Method method,Boundary db,JSONObject command) throws Exception {
    AppRuntime.validate(command);
    try { method.invoke(null,db.connection(),command); }
    catch(InvocationTargetException e) { if(e.getCause() instanceof Exception x) throw x; throw e; }
    require(db.balanceBindings!=null && db.historyBindings!=null,"Missing real writes");
    require(db.events.length()==(db.customer.get("c_credit").equals("BC")?9:8),"Unexpected branch call count");
  }
  static String codePointPrefix(String s,int n) { return s.substring(0,s.offsetByCodePoints(0,Math.min(n,s.codePointCount(0,s.length())))); }
  public static void main(String[] args) throws Exception {
    Class<?> adapter=Class.forName("com.oltpbenchmark.api.TpccApp");
    Class<?> payment=Class.forName("com.oltpbenchmark.benchmarks.tpcc.procedures.Payment");
    require(Path.of(adapter.getProtectionDomain().getCodeSource().getLocation().toURI()).equals(Path.of(args[1])),"Wrong adapter");
    require(Path.of(payment.getProtectionDomain().getCodeSource().getLocation().toURI()).equals(Path.of(args[2])),"Wrong Payment");
    Method entry=adapter.getDeclaredMethod("reference",Connection.class,JSONObject.class); entry.setAccessible(true);
    JSONArray cases=new JSONArray();
    for(String balance:List.of("10.00","262144.00")) {
      Boundary db=new Boundary("GC",balance,"data"); execute(entry,db,command("float-"+balance,"0.01"));
      BigDecimal bound=BigDecimal.valueOf((Double)db.balanceBindings.get(1));
      BigDecimal cents=bound.setScale(2,RoundingMode.HALF_UP), naive=new BigDecimal(balance).subtract(new BigDecimal("0.01"));
      boolean different=cents.compareTo(naive)!=0;
      require(different==balance.equals("262144.00"),"Unexpected float witness");
      cases.put(new JSONObject().put("kind","float32_customer_arithmetic").put("initial",balance)
        .put("actual_jdbc_double",bound.toPlainString()).put("rounded_to_cents",cents.toPlainString())
        .put("naive_decimal_subtraction",naive.toPlainString()).put("different_at_cents",different).put("calls",db.events));
    }
    Boundary text=new Boundary("BC","10.00","data"); execute(entry,text,command("float-text","0.10"));
    String naive="1 1 1 1 1 0.10 | data";
    require(!text.storedCdata.equals(naive),"Missing Float.toString witness");
    cases.put(new JSONObject().put("kind","float_to_text").put("actual_jdbc_string",text.storedCdata).put("naive_decimal_text",naive).put("calls",text.events));
    String prefix="1 1 1 1 1 0.1 | ";
    String data="a".repeat(498-prefix.length())+"😀Ztail";
    Boundary unicode=new Boundary("BC","10.00",data); execute(entry,unicode,command("utf16-data","0.10"));
    String cp=codePointPrefix(prefix+data,500);
    require(unicode.storedCdata.length()==500 && unicode.storedCdata.endsWith("😀") && cp.endsWith("Z"),"Bad Unicode fixture");
    cases.put(new JSONObject().put("kind","utf16_cdata_limit").put("actual_utf16_units",unicode.storedCdata.length())
      .put("actual_codepoints",unicode.storedCdata.codePointCount(0,unicode.storedCdata.length()))
      .put("codepoint_prefix_count",cp.codePointCount(0,cp.length())).put("actual_tail","😀").put("codepoint_tail","😀Z").put("calls",unicode.events));
    Boundary names=new Boundary("GC","10.00","data"); names.warehouseName="a".repeat(8)+"😀Z";
    execute(entry,names,command("utf16-name","0.10"));
    String actual=(String)names.historyBindings.get(8), naiveName=codePointPrefix(names.warehouseName,10)+"    "+names.districtName;
    require(!actual.equals(naiveName) && actual.equals("a".repeat(8)+"😀    district"),"Missing name truncation witness");
    cases.put(new JSONObject().put("kind","utf16_history_name").put("actual_jdbc_string",actual).put("naive_codepoint_string",naiveName).put("calls",names.events));
    JSONObject report=new JSONObject().put("status","passed").put("cases",cases).put("actual_adapter_class",adapter.getName())
      .put("scope","Five actual frozen Payment/adapter executions with JDBC boundary doubles; no database, transaction or driver-encoding equivalence claim")
      .put("source_runtime_calls",5).put("new_database_runs",0);
    Files.writeString(Path.of(args[0]),report.toString(2)+"\n");
  }
}
