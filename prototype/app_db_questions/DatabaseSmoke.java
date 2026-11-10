package reversedb.tpcc;

import java.nio.file.*;
import java.sql.*;
import java.util.*;
import org.json.*;

/** Isolated semantic fixture, not a production optimizer or a TPC-C benchmark. */
public final class DatabaseSmoke {
  static String url;
  static final JSONArray records = new JSONArray();
  static final JSONObject report = new JSONObject();
  static final String WINDOW = "CASE WHEN d.d_next_o_id::bigint-20 < -2147483648::bigint "
      + "THEN d.d_next_o_id::bigint-20+4294967296 ELSE d.d_next_o_id::bigint-20 END";
  static final String TARGET = "SELECT row_id,k,label FROM targets WHERE tenant=1 AND visible";
  static final String EFFECTIVE = "l.tenant=1 AND NOT l.deleted AND l.doc IN (SELECT doc FROM allowed WHERE tenant=1)";

  static void check(boolean condition, String message) {
    if (!condition) throw new AssertionError(message);
  }
  static Connection connect(boolean readOnly) throws Exception {
    Connection c = DriverManager.getConnection(url, "research", "local-fixture-only");
    c.setAutoCommit(false);
    c.setTransactionIsolation(Connection.TRANSACTION_SERIALIZABLE);
    c.setReadOnly(readOnly);
    return c;
  }
  static void sql(Connection c, String text) throws Exception {
    try (Statement s=c.createStatement()) { s.execute(text); }
  }
  static JSONArray rows(Connection c, String query, Object... args) throws Exception {
    try (PreparedStatement s=Sql.bind(c,query,args); ResultSet r=s.executeQuery()) {
      JSONArray result=new JSONArray();
      while(r.next()) {
        JSONArray row=new JSONArray();
        for(int i=1;i<=r.getMetaData().getColumnCount();i++) {
          Object value=r.getObject(i);
          row.put(new JSONObject().put("value",value==null?JSONObject.NULL:value)
              .put("java_type",value==null?"null":value.getClass().getName())
              .put("jdbc_type",r.getMetaData().getColumnType(i)));
        }
        result.put(row);
      }
      return result;
    }
  }
  static String state(Connection c) throws Exception {
    JSONArray all=new JSONArray();
    for(String table:List.of("district","stock","order_line","targets","links","allowed"))
      all.put(rows(c,"SELECT row_to_json(t)::text FROM "+table+" t ORDER BY row_to_json(t)::text"));
    return all.toString();
  }
  interface IntQuery { int run() throws Exception; }
  static JSONObject outcome(Connection c, IntQuery query) throws Exception {
    Savepoint save=c.setSavepoint();
    try {
      int value=query.run();
      return new JSONObject().put("status","value").put("value",value).put("java_type","java.lang.Integer");
    } catch(SQLException ex) {
      c.rollback(save);
      return new JSONObject().put("status","error").put("class",ex.getClass().getName())
          .put("message",ex.getMessage()).put("sqlstate",ex.getSQLState()==null?JSONObject.NULL:ex.getSQLState());
    } finally { c.releaseSavepoint(save); }
  }
  static String scalarQuery(String lower, boolean distinct, String upper) {
    return "SELECT (SELECT count("+(distinct?"DISTINCT ":"")+"s.s_i_id) "
      +"FROM order_line l JOIN stock s ON s.s_w_id=1 AND s.s_i_id=l.ol_i_id "
      +"WHERE l.ol_w_id=1 AND l.ol_d_id=d.d_id AND l.ol_o_id >= ("+lower+") "
      +"AND l.ol_o_id "+upper+" d.d_next_o_id AND s.s_quantity < ?) "
      +"FROM district d WHERE d.d_w_id=1 AND d.d_id=?";
  }
  static int scalar(Connection c, String query, int district, int threshold) throws Exception {
    try(PreparedStatement s=Sql.bind(c,query,threshold,district);ResultSet r=s.executeQuery()) {
      if(!r.next()) throw new SQLException("Missing district");
      return r.getInt(1); // Preserve the source's final JDBC decoder.
    }
  }
  static void scalarFixture(Integer next, boolean populated) throws Exception {
    try(Connection c=connect(false)) {
      sql(c,"TRUNCATE district,stock,order_line");
      if(next!=null) {
        try(PreparedStatement s=Sql.bind(c,"INSERT INTO district VALUES (1,1,?)",next)) { s.executeUpdate(); }
        if(populated) {
          // Boundary orders, a repeated item, a quantity equal to the threshold,
          // another district, and another warehouse are all deliberate.
          Set<Integer> orders=new LinkedHashSet<>(List.of(Integer.MIN_VALUE,next-20,next));
          if(next>Integer.MIN_VALUE) orders.add(next-1);
          if((long)next-21>=Integer.MIN_VALUE) orders.add(next-21);
          int item=1, number=1;
          for(int order:orders) {
            sql(c,"INSERT INTO stock VALUES (1,"+item+",5)");
            sql(c,"INSERT INTO order_line VALUES (1,1,"+order+","+(number++)+","+item+")");
            if(order==next-1) sql(c,"INSERT INTO order_line VALUES (1,1,"+order+","+(number++)+","+item+")");
            item++;
          }
          sql(c,"INSERT INTO stock VALUES (1,90,10),(1,91,5),(2,92,5)");
          int inside=next>Integer.MIN_VALUE?next-1:next;
          sql(c,"INSERT INTO order_line VALUES (1,1,"+inside+",100,90),(1,2,"+inside+",101,91),(2,1,"+inside+",102,92)");
        }
      }
      c.commit();
    }
  }
  static int oracle(Connection c, int next) throws Exception {
    Set<Integer> items=new HashSet<>();
    // Independent scan and host predicate evaluation, without the candidate SQL.
    Map<Integer,Integer> stock=new HashMap<>();
    try(Statement s=c.createStatement();ResultSet r=s.executeQuery("SELECT s_i_id,s_quantity FROM stock WHERE s_w_id=1")) {
      while(r.next()) stock.put(r.getInt(1),r.getInt(2));
    }
    try(Statement s=c.createStatement();ResultSet r=s.executeQuery("SELECT ol_o_id,ol_i_id FROM order_line WHERE ol_w_id=1 AND ol_d_id=1")) {
      while(r.next()) {
        int order=r.getInt(1),item=r.getInt(2);
        if(order>=next-20 && order<next && stock.containsKey(item) && stock.get(item)<10) items.add(item);
      }
    }
    return items.size();
  }
  static void scalarTests() throws Exception {
    Integer[] nexts={null,30,Integer.MIN_VALUE,Integer.MIN_VALUE+1,Integer.MIN_VALUE+19,
                    Integer.MIN_VALUE+20,0,19,20,Integer.MAX_VALUE};
    for(int i=0;i<nexts.length;i++) {
      Integer next=nexts[i];
      scalarFixture(next,i!=1);
      try(Connection c=connect(true)) {
        String before=state(c);
        JSONObject original=outcome(c,()->new StockLevelService().count(c,1,10));
        JSONObject candidate=outcome(c,()->scalar(c,scalarQuery(WINDOW,true,"<"),1,10));
        check(original.similar(candidate),"Scalar differential: "+next);
        if(next!=null) check(original.getInt("value")==oracle(c,next),"Independent scalar oracle");
        check(before.equals(state(c)),"Scalar state changed");
        records.put(new JSONObject().put("kind","scalar_differential").put("next",next==null?JSONObject.NULL:next)
          .put("original",original).put("candidate",candidate).put("state_unchanged",true));
        c.commit();
      }
    }
    // Ordinary nonempty fixture separately from the empty n=30 fixture above.
    scalarFixture(30,true);
    try(Connection c=connect(true)) {
      JSONObject original=outcome(c,()->new StockLevelService().count(c,1,10));
      JSONObject candidate=outcome(c,()->scalar(c,scalarQuery(WINDOW,true,"<"),1,10));
      check(original.similar(candidate) && original.getInt("value")==oracle(c,30),"Ordinary scalar");
      records.put(new JSONObject().put("kind","scalar_differential").put("case","ordinary_nonempty")
        .put("original",original).put("candidate",candidate));
      for(String mutant:List.of("no_distinct","inclusive_upper")) {
        JSONObject wrong=outcome(c,()->scalar(c,scalarQuery(WINDOW,!mutant.equals("no_distinct"),mutant.equals("inclusive_upper")?"<=":"<"),1,10));
        check(!original.similar(wrong),"Scalar mutant survived: "+mutant);
        records.put(new JSONObject().put("kind","mutant").put("name",mutant).put("original",original).put("wrong",wrong));
      }
      c.commit();
    }
    scalarFixture(Integer.MIN_VALUE+1,true);
    try(Connection c=connect(true)) {
      JSONObject original=outcome(c,()->new StockLevelService().count(c,1,10));
      JSONObject widened=outcome(c,()->scalar(c,scalarQuery("d.d_next_o_id::bigint-20",true,"<"),1,10));
      JSONObject narrow=outcome(c,()->scalar(c,scalarQuery("d.d_next_o_id-20",true,"<"),1,10));
      check(!original.similar(widened),"Widened arithmetic mutant survived");
      check(narrow.getString("sqlstate").equals("22003"),"Expected PostgreSQL integer overflow");
      records.put(new JSONObject().put("kind","mutant").put("name","widened_without_wrap").put("original",original).put("wrong",widened));
      records.put(new JSONObject().put("kind","mutant").put("name","sql_int_subtraction").put("original",original).put("wrong",narrow));
      c.commit();
    }
    scalarFixture(null,false);
    try(Connection c=connect(true)) {
      JSONObject original=outcome(c,()->new StockLevelService().count(c,1,10));
      JSONObject wrong=outcome(c,()->scalar(c,"SELECT COALESCE(("+scalarQuery(WINDOW,true,"<")+"),0)",1,10));
      check(!original.similar(wrong),"Missing district masked");
      records.put(new JSONObject().put("kind","mutant").put("name","missing_to_zero").put("original",original).put("wrong",wrong));
      c.commit();
    }
  }

  static String page(int offset,int limit) { return TARGET+" ORDER BY row_id LIMIT "+limit+" OFFSET "+offset; }
  static String counts(String keyQuery) {
    return "SELECT l.k,count(DISTINCT l.doc) AS n FROM links l WHERE "+EFFECTIVE
      +" AND l.k IN (SELECT k FROM ("+keyQuery+") keys) GROUP BY l.k";
  }
  static Map<Integer,Long> materialize(Connection c,String query) throws Exception {
    Map<Integer,Long> result=new TreeMap<>();
    try(Statement s=c.createStatement();ResultSet r=s.executeQuery(query)) {
      while(r.next()) result.put(r.getInt(1),r.getLong(2));
    }
    return result;
  }
  static JSONArray mapped(Connection c,Map<Integer,Long> map,int offset,int limit,boolean countOrder) throws Exception {
    StringBuilder expr=new StringBuilder();
    List<Object> args=new ArrayList<>();
    if(!map.isEmpty()) {
      expr.append("CASE ");
      for(var entry:map.entrySet()) {
        expr.append("WHEN t.k=? THEN CAST(? AS bigint) ");args.add(entry.getKey());args.add(entry.getValue());
      }
      expr.append("ELSE 0::bigint END");
    } else expr.append("0::bigint");
    return rows(c,"SELECT t.row_id,t.k,t.label,"+expr+" AS document_count FROM ("+TARGET+") t ORDER BY "
      +(countOrder?"document_count DESC,":"")+"t.row_id LIMIT "+limit+" OFFSET "+offset,args.toArray());
  }
  static JSONArray arm(Connection c,String mode,int offset,int limit) throws Exception {
    boolean reduced=mode.equals("p2")||mode.equals("both");
    String countQuery=counts(reduced?page(offset,limit):TARGET);
    if(mode.equals("original")||mode.equals("p2")) return mapped(c,materialize(c,countQuery),offset,limit,false);
    return rows(c,"WITH w AS ("+page(offset,limit)+"), g AS ("+countQuery+") "
      +"SELECT w.row_id,w.k,w.label,COALESCE(g.n,0::bigint) AS document_count FROM w LEFT JOIN g ON g.k=w.k ORDER BY w.row_id");
  }
  static void pageFixture(String kind) throws Exception {
    try(Connection c=connect(false)) {
      sql(c,"TRUNCATE targets,links,allowed");
      if(!kind.equals("empty")) sql(c,"INSERT INTO targets VALUES "
        +"(1,10,'A',true,1),(2,10,NULL,true,1),(3,20,'B',true,1),(4,30,'C',true,1),"
        +"(5,40,'hidden',false,1),(6,50,'other tenant',true,2),(7,60,'D',true,1),(8,70,'E',true,1)");
      sql(c,"INSERT INTO allowed VALUES(1,1),(1,1),(2,1),(4,1),(3,2)");
      if(kind.equals("mixed")) sql(c,"INSERT INTO links VALUES "
        +"(10,1,false,1),(10,1,false,1),(10,2,false,1),(10,3,false,1),"
        +"(20,3,false,1),(20,4,true,1),(30,4,false,1),(30,2,false,2),"
        +"(40,1,false,1),(50,3,false,2),(70,1,false,1),(70,2,false,1),(70,4,false,1)");
      c.commit();
    }
  }
  static JSONArray pageOracle(Connection c,int offset,int limit) throws Exception {
    Set<Integer> allowed=new HashSet<>();
    Map<Integer,Set<Integer>> links=new HashMap<>();
    try(Statement s=c.createStatement();ResultSet r=s.executeQuery("SELECT doc FROM allowed WHERE tenant=1")) {
      while(r.next()) allowed.add(r.getInt(1));
    }
    try(Statement s=c.createStatement();ResultSet r=s.executeQuery("SELECT k,doc,deleted,tenant FROM links")) {
      while(r.next()) if(r.getInt(4)==1&&!r.getBoolean(3)&&allowed.contains(r.getInt(2)))
        links.computeIfAbsent(r.getInt(1),k->new HashSet<>()).add(r.getInt(2));
    }
    JSONArray result=new JSONArray();
    try(Statement s=c.createStatement();ResultSet r=s.executeQuery(TARGET+" ORDER BY row_id")) {
      int index=0;
      while(r.next()) {
        if(index>=offset && (long)index<((long)offset+limit)) result.put(new JSONArray()
          .put(r.getInt(1)).put(r.getInt(2)).put(r.getString(3)==null?JSONObject.NULL:r.getString(3))
          .put((long)links.getOrDefault(r.getInt(2),Set.of()).size()));
        index++;
      }
    }
    return result;
  }
  static JSONArray values(JSONArray typed) {
    JSONArray values=new JSONArray();
    for(Object row:typed) {
      JSONArray out=new JSONArray();for(Object cell:(JSONArray)row) out.put(((JSONObject)cell).get("value"));values.put(out);
    }
    return values;
  }
  static void pageTests() throws Exception {
    int[][] pages={{0,0},{0,1},{1,2},{3,2},{99,2},{0,100}};
    for(String kind:List.of("empty","no_links","mixed")) {
      pageFixture(kind);
      for(int[] p:pages) try(Connection c=connect(true)) {
        String before=state(c);
        JSONObject variants=new JSONObject();
        JSONArray original=arm(c,"original",p[0],p[1]);
        check(values(original).similar(pageOracle(c,p[0],p[1])),"Independent page oracle");
        for(String mode:List.of("original","p1","p2","both")) {
          JSONArray actual=arm(c,mode,p[0],p[1]);
          check(original.similar(actual),"Page mismatch: "+kind+"/"+mode+"/"+Arrays.toString(p));
          variants.put(mode,actual);
        }
        check(before.equals(state(c)),"Page state changed");
        JSONArray work=rows(c,"SELECT "
          +"(SELECT count(*) FROM links l WHERE "+EFFECTIVE+" AND l.k IN (SELECT k FROM ("+TARGET+") all_keys)) AS full_input, "
          +"(SELECT count(*) FROM links l WHERE "+EFFECTIVE+" AND l.k IN (SELECT k FROM ("+page(p[0],p[1])+") page_keys)) AS demanded_input");
        records.put(new JSONObject().put("kind","page_four_arm").put("fixture",kind)
          .put("offset",p[0]).put("limit",p[1]).put("variants",variants).put("logical_inputs",work).put("state_unchanged",true));
        c.commit();
      }
    }
    pageFixture("mixed");
    try(Connection c=connect(true)) {
      Map<Integer,Long> full=materialize(c,counts(TARGET));
      JSONArray correct=mapped(c,full,0,1,true);
      JSONArray wrong=arm(c,"both",0,1);
      check(!correct.similar(wrong),"Count-order mutation survived");
      records.put(new JSONObject().put("kind","mutant").put("name","page_before_count_sort")
        .put("original",correct).put("wrong",wrong));
      // A concrete total-count consumer, including repeated target occurrences.
      JSONArray all=values(mapped(c,full,0,100,false));
      JSONArray pruned=values(mapped(c,materialize(c,counts(page(0,1))),0,100,false));
      long total=0,wrongTotal=0;
      for(Object row:all) total+=((JSONArray)row).getLong(3);
      for(Object row:pruned) wrongTotal+=((JSONArray)row).getLong(3);
      check(total!=wrongTotal,"Global-consumer mutation survived");
      records.put(new JSONObject().put("kind","mutant").put("name","omitted_global_count_consumer")
        .put("original",total).put("wrong",wrongTotal));
      // Decisions are explicit fixture descriptors, not source inference.
      for(String consumer:List.of("count_order","global_count","unknown")) {
        String decision=demandDecision(consumer);
        check(!decision.equals("accept"),"Unsafe consumer admitted");
        JSONObject original=originalConsumer(c,consumer);
        JSONObject fallback=routedConsumer(c,consumer);
        check(original.similar(fallback.getJSONObject("result")),"Fallback output mismatch");
        check(fallback.getString("route").equals("original"),"Fallback route mismatch");
        records.put(new JSONObject().put("kind","consumer_admission").put("consumer",consumer)
          .put("decision",decision).put("output",fallback));
      }
      check(demandDecision("base_order_page").equals("accept"),"Explicit consumer not admitted");
      JSONObject accepted=routedConsumer(c,"base_order_page");
      check(accepted.getString("route").equals("candidate") && accepted.getJSONObject("result").similar(originalConsumer(c,"base_order_page")),"Accepted consumer routing");
      records.put(new JSONObject().put("kind","consumer_admission").put("consumer","base_order_page")
        .put("decision","accept").put("output",accepted));
      c.commit();
    }
  }
  static String demandDecision(String consumer) {
    return switch(consumer) {case "base_order_page"->"accept";case "count_order","global_count"->"reject";default->"unknown";};
  }
  static JSONObject originalConsumer(Connection c,String consumer) throws Exception {
    Map<Integer,Long> full=materialize(c,counts(TARGET));
    JSONObject response=new JSONObject().put("rows",mapped(c,full,0,1,consumer.equals("count_order")));
    if(consumer.equals("global_count")) {
      long total=0;
      for(Object row:values(mapped(c,full,0,100,false))) total+=((JSONArray)row).getLong(3);
      response.put("total",total);
    }
    return response;
  }
  static JSONObject routedConsumer(Connection c,String consumer) throws Exception {
    if(!demandDecision(consumer).equals("accept"))
      return new JSONObject().put("route","original").put("result",originalConsumer(c,consumer));
    return new JSONObject().put("route","candidate").put("result",new JSONObject().put("rows",arm(c,"both",0,1)));
  }
  static void schema() throws Exception {
    try(Connection c=connect(false)) {
      sql(c,"CREATE TABLE district(d_w_id int NOT NULL,d_id int NOT NULL,d_next_o_id int NOT NULL,PRIMARY KEY(d_w_id,d_id))");
      sql(c,"CREATE TABLE stock(s_w_id int NOT NULL,s_i_id int NOT NULL,s_quantity int NOT NULL,PRIMARY KEY(s_w_id,s_i_id))");
      sql(c,"CREATE TABLE order_line(ol_w_id int NOT NULL,ol_d_id int NOT NULL,ol_o_id int NOT NULL,ol_number int NOT NULL,ol_i_id int NOT NULL,PRIMARY KEY(ol_w_id,ol_d_id,ol_o_id,ol_number))");
      sql(c,"CREATE TABLE targets(row_id int PRIMARY KEY,k int NOT NULL,label text,visible boolean NOT NULL,tenant int NOT NULL)");
      sql(c,"CREATE TABLE links(k int NOT NULL,doc int NOT NULL,deleted boolean NOT NULL,tenant int NOT NULL)");
      sql(c,"CREATE TABLE allowed(doc int NOT NULL,tenant int NOT NULL)");
      c.commit();
    }
    try(Connection c=connect(true)) {
      report.put("database",rows(c,"SELECT version(),current_setting('transaction_isolation'),current_setting('transaction_read_only')"));
      report.put("driver",c.getMetaData().getDriverVersion());
      report.put("columns",rows(c,"SELECT table_name,column_name,data_type,is_nullable FROM information_schema.columns WHERE table_schema='public' ORDER BY table_name,ordinal_position"));
      report.put("constraints",rows(c,"SELECT conrelid::regclass::text,pg_get_constraintdef(oid) FROM pg_constraint WHERE connamespace='public'::regnamespace ORDER BY 1,2"));
      JSONObject probe=outcome(c,()->{sql(c,"INSERT INTO allowed VALUES(999,1)");return 1;});
      check(probe.getString("sqlstate").equals("25006"),"Read-only transaction not enforced");
      report.put("read_only_probe",probe);c.commit();
    }
  }
  public static void main(String[] args) throws Exception {
    url=args[0];Path output=Path.of(args[1]);
    report.put("status","running").put("scope","isolated reduced-schema JDBC fixtures; no performance or full API claim")
      .put("records",records).put("scalar_sql",scalarQuery(WINDOW,true,"<"));
    try {
      schema();scalarTests();pageTests();
      JSONObject totals=new JSONObject();
      for(Object entry:records) {String kind=((JSONObject)entry).getString("kind");totals.put(kind,totals.optInt(kind)+1);}
      report.put("status","passed").put("totals",totals);
      System.out.println(totals);
    } catch(Throwable error) {
      report.put("status","failed").put("failure",error.toString());throw error;
    } finally {Files.writeString(output,report.toString(2)+"\n");}
  }
}
