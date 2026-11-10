package reversedb.tpcc;

import java.io.*;
import java.lang.management.*;
import java.lang.reflect.*;
import java.net.HttpURLConnection;
import java.net.URI;
import java.nio.charset.StandardCharsets;
import java.nio.file.*;
import java.security.*;
import java.sql.*;
import java.util.*;
import org.json.*;

/** Fixed cost experiment; first four arms reuse the archived semantic fixture. */
public final class DatabaseCosts {
  static JSONObject protocol;
  static final JSONObject report=new JSONObject();
  static final JSONArray cases=new JSONArray();
  static final List<String> arms=new ArrayList<>();
  static Path output;
  static String directUrl,proxyUrl,counterUrl;
  static Random order;

  record Query(String sql,List<Object> args) {}
  static void check(boolean ok,String message) { if(!ok) throw new AssertionError(message); }
  static String hash(String value) throws Exception {
    return HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(value.getBytes(StandardCharsets.UTF_8)));
  }
  static void save() throws Exception { Files.writeString(output,report.toString(2)+"\n"); }
  static Connection connect(String url) throws Exception {
    Connection c=DriverManager.getConnection(url,"research","local-fixture-only");
    c.setAutoCommit(false);c.setTransactionIsolation(Connection.TRANSACTION_SERIALIZABLE);c.setReadOnly(true);
    return c;
  }
  static Object invoke(Object target,Method method,Object[] args) throws Throwable {
    try {return method.invoke(target,args);} catch(InvocationTargetException error) {throw error.getCause();}
  }
  static Connection capture(Connection raw,List<Query> queries) {
    return (Connection)Proxy.newProxyInstance(DatabaseCosts.class.getClassLoader(),new Class<?>[]{Connection.class},(p,m,a)->{
      Object result=invoke(raw,m,a);
      if(m.getName().equals("prepareStatement")) {
        String sql=(String)a[0];TreeMap<Integer,Object> params=new TreeMap<>();
        return Proxy.newProxyInstance(DatabaseCosts.class.getClassLoader(),new Class<?>[]{PreparedStatement.class},(sp,sm,sa)->{
          if(sm.getName().equals("setObject")) params.put((Integer)sa[0],sa[1]);
          if(sm.getName().equals("executeQuery")) queries.add(new Query(sql,new ArrayList<>(params.values())));
          return invoke(result,sm,sa);
        });
      }
      if(m.getName().equals("createStatement")) return Proxy.newProxyInstance(DatabaseCosts.class.getClassLoader(),new Class<?>[]{Statement.class},(sp,sm,sa)->{
        if(sm.getName().equals("executeQuery")) queries.add(new Query((String)sa[0],List.of()));
        return invoke(result,sm,sa);
      });
      return result;
    });
  }
  static String execute(Connection c,String arm,int n,int limit) throws Exception {
    check(n>0 && limit>=0 && limit<=n,"Unbound fixture input");
    check(DatabaseSmoke.demandDecision("base_order_page").equals("accept"),"Unknown consumer");
    JSONArray rows;
    if(List.of("original","p1","p2","both").contains(arm)) rows=DatabaseSmoke.arm(c,arm,0,limit);
    else if(arm.equals("manual_grouped_join")) rows=DatabaseSmoke.rows(c,
      "SELECT w.row_id,w.k,w.label,count(DISTINCT l.doc) AS document_count FROM ("+DatabaseSmoke.page(0,limit)+") w "
      +"LEFT JOIN links l ON l.k=w.k AND "+DatabaseSmoke.EFFECTIVE
      +" GROUP BY w.row_id,w.k,w.label ORDER BY w.row_id");
    else if(arm.equals("manual_correlated_subquery")) rows=DatabaseSmoke.rows(c,
      "SELECT w.row_id,w.k,w.label,(SELECT count(DISTINCT l.doc) FROM links l WHERE l.k=w.k AND "
      +DatabaseSmoke.EFFECTIVE+") AS document_count FROM ("+DatabaseSmoke.page(0,limit)+") w ORDER BY w.row_id");
    else throw new IllegalArgumentException(arm);
    return rows.toString();
  }
  static void fixture(int n,int density,int seed) throws Exception {
    try(Connection c=DatabaseSmoke.connect(false)) {
      DatabaseSmoke.sql(c,"TRUNCATE targets,links,allowed");
      Random rng=new Random(seed);List<Integer> keys=new ArrayList<>();
      for(int i=1;i<=n;i++) keys.add(i);Collections.shuffle(keys,rng);
      int maxDoc=4*density;
      try(PreparedStatement target=c.prepareStatement("INSERT INTO targets VALUES(?,?,?,true,1)");
          PreparedStatement link=c.prepareStatement("INSERT INTO links VALUES(?,?,?,?)");
          PreparedStatement allowed=c.prepareStatement("INSERT INTO allowed VALUES(?,?)")) {
        for(int doc=1;doc<=maxDoc;doc++) {allowed.setInt(1,doc);allowed.setInt(2,1);allowed.addBatch();}
        allowed.setInt(1,999999);allowed.setInt(2,2);allowed.addBatch();allowed.executeBatch();
        for(int key:keys) {
          target.setInt(1,key);target.setInt(2,key);target.setString(3,key%7==0?null:"row-"+key);target.addBatch();
          for(int j=0;j<density;j++) {
            link.setInt(1,key);link.setInt(2,1+rng.nextInt(maxDoc));link.setBoolean(3,false);link.setInt(4,1);link.addBatch();
          }
          link.setInt(1,key);link.setInt(2,999999);link.setBoolean(3,false);link.setInt(4,1);link.addBatch();
          link.setInt(2,1);link.setBoolean(3,true);link.addBatch();
          link.setBoolean(3,false);link.setInt(4,2);link.addBatch();
        }
        target.executeBatch();link.executeBatch();
      }
      DatabaseSmoke.sql(c,"ANALYZE targets");DatabaseSmoke.sql(c,"ANALYZE links");DatabaseSmoke.sql(c,"ANALYZE allowed");
      c.commit();
    }
  }
  static JSONObject counters() throws Exception {
    HttpURLConnection connection=(HttpURLConnection)URI.create(counterUrl).toURL().openConnection();
    connection.setConnectTimeout(5000);connection.setReadTimeout(5000);
    try(InputStream in=connection.getInputStream()) {
      return new JSONObject(new String(in.readAllBytes(),StandardCharsets.UTF_8));
    } finally {connection.disconnect();}
  }
  static long heapUsed() {return ManagementFactory.getMemoryMXBean().getHeapMemoryUsage().getUsed();}
  static List<MemoryPoolMXBean> heapPools() {
    return ManagementFactory.getMemoryPoolMXBeans().stream().filter(p->p.getType()==MemoryType.HEAP).toList();
  }
  static JSONObject memory(long before) {
    JSONArray pools=new JSONArray();long sum=0;
    for(MemoryPoolMXBean pool:heapPools()) {
      long peak=pool.getPeakUsage().getUsed();sum+=peak;
      pools.put(new JSONObject().put("name",pool.getName()).put("peak_used_bytes",peak));
    }
    return new JSONObject().put("heap_before_bytes",before).put("heap_after_bytes",heapUsed())
      .put("sum_heap_pool_peaks_bytes",sum).put("peak_sum_minus_before_bytes",Math.max(0,sum-before)).put("pools",pools);
  }
  static JSONObject explain(Connection c,Query query) throws Exception {
    try(PreparedStatement s=Sql.bind(c,"EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) "+query.sql(),query.args().toArray());ResultSet r=s.executeQuery()) {
      check(r.next(),"Missing EXPLAIN");return new JSONArray(r.getString(1)).getJSONObject(0);
    }
  }
  static JSONArray diagnostics(Connection plans,int n,int limit,String expected) throws Exception {
    JSONArray result=new JSONArray();
    try(Connection network=connect(proxyUrl)) {
      // Start the transaction before measuring stream bytes; no concurrent proxy users.
      DatabaseSmoke.rows(network,"SELECT current_setting('transaction_read_only')");
      for(String arm:arms) {
        for(int i=0;i<2;i++) check(execute(network,arm,n,limit).equals(expected),"Diagnostic warmup mismatch");
        JSONObject before=counters();System.gc();
        for(MemoryPoolMXBean pool:heapPools()) pool.resetPeakUsage();
        long heapBefore=heapUsed();
        String actual=execute(network,arm,n,limit);
        JSONObject mem=memory(heapBefore);JSONObject after=counters();
        check(actual.equals(expected),"Diagnostic typed output mismatch");
        // Statement capture allocates parameter maps, so keep it out of both
        // memory/transport diagnostics and the timed pass.
        List<Query> queries=new ArrayList<>();
        check(execute(capture(plans,queries),arm,n,limit).equals(expected),"Captured output mismatch");
        JSONObject entry=new JSONObject().put("arm",arm).put("memory",mem)
          .put("client_to_server_bytes",after.getLong("client_to_server_bytes")-before.getLong("client_to_server_bytes"))
          .put("server_to_client_bytes",after.getLong("server_to_client_bytes")-before.getLong("server_to_client_bytes"))
          .put("jdbc_execute_calls",queries.size());
        JSONArray statements=new JSONArray();
        for(Query query:queries) statements.put(new JSONObject().put("sql",query.sql()).put("parameters",query.args()).put("explain",explain(plans,query)));
        entry.put("statements",statements);result.put(entry);
      }
      network.commit();
    }
    return result;
  }
  static void testCase(int n,int density,int percent,int seed) throws Exception {
    fixture(n,density,seed);int limit=n*percent/100;
    JSONObject record=new JSONObject().put("targets",n).put("density",density).put("page_percent",percent).put("seed",seed).put("limit",limit);
    cases.put(record);JSONArray samples=new JSONArray();record.put("samples",samples);
    try(Connection c=connect(directUrl)) {
      long preflightStart=System.nanoTime();
      String stateBefore=hash(DatabaseSmoke.state(c));
      String expected=execute(c,"original",n,limit);
      check(DatabaseSmoke.values(new JSONArray(expected)).similar(DatabaseSmoke.pageOracle(c,0,limit)),"Independent oracle mismatch");
      for(String arm:arms) check(execute(c,arm,n,limit).equals(expected),"Preflight output mismatch: "+arm);
      record.put("preflight_validation_ns",System.nanoTime()-preflightStart).put("expected_output_sha256",hash(expected))
        .put("expected_typed_output",new JSONArray(expected)).put("fixture_state_sha256",stateBefore);
      try(Statement s=c.createStatement();ResultSet r=s.executeQuery("SELECT "
          +"(SELECT count(*) FROM links l WHERE "+DatabaseSmoke.EFFECTIVE+") AS full_input, "
          +"(SELECT count(*) FROM links l WHERE "+DatabaseSmoke.EFFECTIVE+" AND l.k IN (SELECT k FROM ("+DatabaseSmoke.page(0,limit)+") w)) AS demanded_input")) {
        check(r.next() && r.getLong(1)==(long)n*density && r.getLong(2)==(long)limit*density,"Logical input cardinality mismatch");
        record.put("full_logical_input",r.getLong(1)).put("demanded_logical_input",r.getLong(2));
      }
      for(int warm=0;warm<protocol.getInt("warmups_per_arm");warm++) {
        List<String> schedule=new ArrayList<>(arms);Collections.shuffle(schedule,order);
        for(String arm:schedule) check(execute(c,arm,n,limit).equals(expected),"Warmup mismatch");
      }
      for(int block=0;block<protocol.getInt("blocks");block++) {
        List<String> schedule=new ArrayList<>(arms);Collections.shuffle(schedule,order);
        for(int position=0;position<schedule.size();position++) {
          String arm=schedule.get(position);long started=System.nanoTime();
          String actual=execute(c,arm,n,limit);
          long elapsed=System.nanoTime()-started;
          check(actual.equals(expected),"Timed output mismatch: "+arm);
          samples.put(new JSONObject().put("block",block).put("position",position).put("arm",arm).put("elapsed_ns",elapsed).put("output_verified",true));
        }
      }
      record.put("diagnostics",diagnostics(c,n,limit,expected));
      check(stateBefore.equals(hash(DatabaseSmoke.state(c))),"Fixture mutated");
      record.put("state_unchanged",true);c.commit();
    }
    save();System.out.println("Completed n="+n+" d="+density+" page="+percent+" seed="+seed);System.out.flush();
  }
  public static void main(String[] args) throws Exception {
    directUrl=args[0];proxyUrl=args[1];counterUrl=args[2];output=Path.of(args[3]);
    protocol=new JSONObject(Files.readString(Path.of(args[4])));
    for(Object arm:protocol.getJSONArray("arms")) arms.add((String)arm);
    order=new Random(protocol.getLong("arm_order_seed"));DatabaseSmoke.url=directUrl;
    report.put("status","running").put("protocol",protocol).put("cases",cases)
      .put("cost_scope","hand-coded fixture invocation; production analysis/live binding not measured");
    try {
      DatabaseSmoke.schema();report.put("environment",DatabaseSmoke.report);
      try(Connection c=DatabaseSmoke.connect(false)) {
        DatabaseSmoke.sql(c,"CREATE INDEX targets_context_order ON targets(tenant,visible,row_id) INCLUDE(k,label)");
        DatabaseSmoke.sql(c,"CREATE INDEX links_context_key_doc ON links(tenant,k,doc) INCLUDE(deleted)");
        DatabaseSmoke.sql(c,"CREATE INDEX allowed_context_doc ON allowed(tenant,doc)");c.commit();
      }
      for(Object n:protocol.getJSONArray("target_counts"))
        for(Object density:protocol.getJSONArray("effective_links_per_target"))
          for(Object percent:protocol.getJSONArray("page_percentages"))
            for(Object seed:protocol.getJSONArray("data_seeds")) testCase((Integer)n,(Integer)density,(Integer)percent,(Integer)seed);
      int samples=0,diag=0;for(Object item:cases) {samples+=((JSONObject)item).getJSONArray("samples").length();diag+=((JSONObject)item).getJSONArray("diagnostics").length();}
      check(samples==protocol.getInt("expected_timed_samples") && diag==protocol.getInt("expected_diagnostic_calls"),"Coverage mismatch");
      report.put("status","passed").put("timed_samples",samples).put("diagnostic_calls",diag);
    } catch(Throwable error) {report.put("status","failed").put("failure",error.toString());throw error;}
    finally {save();}
  }
}
