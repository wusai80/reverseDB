package research;

import java.lang.reflect.*;
import java.nio.file.*;
import java.sql.*;
import java.util.*;
import org.json.*;
import com.oltpbenchmark.api.Procedure;
import com.oltpbenchmark.types.DatabaseType;
import com.oltpbenchmark.benchmarks.tpcc.procedures.*;
import com.oltpbenchmark.benchmarks.tpcc.pojo.Customer;
import com.oltpbenchmark.benchmarks.tpcc.pojo.Oorder;

/** Runs actual decompiled helper bodies. Supplies rows, does not execute SQL. */
public final class ResultConsumptionProbe {
  static Object proxy(Class<?> type,InvocationHandler h) { return Proxy.newProxyInstance(ResultConsumptionProbe.class.getClassLoader(),new Class<?>[]{type},h); }
  static Object identity(Object p,Method m,Object[] a) {
    return switch(m.getName()) { case "toString" -> "StrictResultBoundary";case "hashCode" -> System.identityHashCode(p);case "equals" -> p==a[0];default -> throw new UnsupportedOperationException(m.toString()); };
  }
  static final class Boundary {
    final List<Integer> rows;final JSONArray events=new JSONArray();int index=-1;boolean wasNull;
    Boundary(List<Integer> rows) { this.rows=rows; }
    Connection connection() {
      return (Connection)proxy(Connection.class,(p,m,a)-> {
        if(m.getDeclaringClass()==Object.class)return identity(p,m,a);
        if(m.getName().equals("prepareStatement")) {
          events.put(new JSONObject().put("op","prepareStatement").put("sql",a[0]));
          return proxy(PreparedStatement.class,(s,n,b)-> {
            if(n.getDeclaringClass()==Object.class)return identity(s,n,b);
            if(n.getName().equals("close") || n.getName().equals("setInt"))return null;
            if(n.getName().equals("executeQuery"))return result();
            throw new UnsupportedOperationException(n.toString());
          });
        }
        throw new UnsupportedOperationException(m.toString());
      });
    }
    ResultSet result() {
      return (ResultSet)proxy(ResultSet.class,(p,m,a)-> {
        if(m.getDeclaringClass()==Object.class)return identity(p,m,a);
        String op=m.getName();
        if(op.equals("close"))return null;
        if(op.equals("next")) { boolean next=++index<rows.size();events.put(new JSONObject().put("op","next").put("value",next));return next; }
        if(op.equals("wasNull"))return wasNull;
        if(index<0 || index>=rows.size())throw new SQLException("getter outside row");
        int row=rows.get(index);String column=((String)a[0]).toUpperCase(Locale.ROOT);
        Object value=switch(column) {
          case "O_ID","NO_O_ID","D_NEXT_O_ID" -> row;
          case "O_CARRIER_ID","OL_SUPPLY_W_ID" -> 1;
          case "OL_I_ID","OL_QUANTITY" -> row;
          case "OL_AMOUNT" -> row+0.25;
          case "O_ENTRY_D","OL_DELIVERY_D" -> new Timestamp(1762300800000L+row);
          default -> throw new UnsupportedOperationException("Unknown column "+column);
        };
        wasNull=value==null;events.put(new JSONObject().put("op",op).put("column",column).put("row",index));
        return switch(op) {
          case "getInt" -> ((Number)value).intValue();case "getLong" -> ((Number)value).longValue();
          case "getDouble" -> ((Number)value).doubleValue();case "getTimestamp" -> value;
          default -> throw new UnsupportedOperationException(m.toString());
        };
      });
    }
  }
  static JSONObject execute(String kind,List<Integer> rows) throws Exception {
    Boundary b=new Boundary(rows);Procedure p=kind.startsWith("order_")?new OrderStatus():kind.equals("delivery_first")?new Delivery():new StockLevel();
    Method init=Procedure.class.getDeclaredMethod("initialize",DatabaseType.class);init.setAccessible(true);init.invoke(p,DatabaseType.POSTGRES);
    String name=kind.equals("order_first")?"getOrderDetails":kind.equals("order_loop")?"getOrderLines":"getOrderId";
    Method method=Arrays.stream(p.getClass().getDeclaredMethods()).filter(m->m.getName().equals(name)).findFirst().orElseThrow();method.setAccessible(true);
    Customer customer=new Customer();customer.c_id=1;customer.c_w_id=1;customer.c_d_id=1;
    Object[] args=kind.equals("order_first")?new Object[]{b.connection(),1,1,customer}:kind.equals("order_loop")?new Object[]{b.connection(),1,1,1,customer}:new Object[]{b.connection(),1,1};
    JSONObject out=new JSONObject().put("method",kind).put("input_rows",rows).put("class_origin",p.getClass().getProtectionDomain().getCodeSource().getLocation().toString());
    try {
      Object result=method.invoke(p,args);
      if(result instanceof Oorder o)result=new JSONObject().put("id",o.o_id).put("carrier",o.o_carrier_id).put("time",o.o_entry_d.toString());
      if(result instanceof List<?> list)result=new JSONArray(list);
      out.put("status","returned").put("result",result==null?JSONObject.NULL:result);
    } catch(InvocationTargetException error) {
      Throwable cause=error.getCause();
      if(!(cause instanceof RuntimeException || cause instanceof SQLException) || cause instanceof UnsupportedOperationException)throw error;
      out.put("status","error").put("error_type",cause.getClass().getName()).put("message",cause.getMessage());
    }
    int next=0;Set<Integer> read=new TreeSet<>();
    for(Object x:b.events) { JSONObject e=(JSONObject)x;if(e.getString("op").equals("next"))next++;if(e.has("row"))read.add(e.getInt("row")); }
    return out.put("next_calls",next).put("read_row_indices",read).put("events",b.events);
  }
  public static void main(String[] args) throws Exception {
    Map<String,List<Integer>> cases=new LinkedHashMap<>();cases.put("empty",List.of());cases.put("one",List.of(101));
    cases.put("two",List.of(101,202));cases.put("five",List.of(101,202,303,404,505));
    cases.put("two_tail_changed",List.of(101,999));cases.put("five_tail_changed",List.of(101,902,903,904,905));cases.put("two_reversed",List.of(202,101));
    JSONArray result=new JSONArray();
    for(String method:List.of("order_first","order_loop","delivery_first","stock_first"))for(var c:cases.entrySet())result.put(execute(method,c.getValue()).put("case",c.getKey()));
    Files.writeString(Path.of(args[0]),new JSONObject().put("runs",result).put("count",result.length())
      .put("scope","real compiled decompiled helpers, supplied JDBC rows; no PostgreSQL, full endpoint, transaction, or concurrency simulation").toString(2)+"\n");
  }
}
