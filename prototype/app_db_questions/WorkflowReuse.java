package reversedb.tpcc;

import java.nio.file.*;
import java.sql.*;
import java.util.*;
import org.json.*;

/** Explicit legal templates and bounded workflow cache; no source inference. */
public final class WorkflowReuse {
  static JSONObject protocol,model;
  static final JSONObject report=new JSONObject();
  static final JSONArray cases=new JSONArray(),semantics=new JSONArray();
  static final List<String> direct=new ArrayList<>();
  static Random order;
  static Path output;
  static String url;
  record Page(int offset,int limit,int tenant) {
    Page {if(offset<0||limit<0||limit>1000||tenant<1||tenant>2) throw new IllegalArgumentException("Unbound page");}
  }
  record Work(Page a,Page b) {}
  record Run(String response,String route,long cacheBytes,int entries) {}
  static void check(boolean ok,String message) {if(!ok) throw new AssertionError(message);}
  static final class Cache implements AutoCloseable {
    private long[] data;
    private final Object owner;
    private int size;
    Cache(int capacity,Object owner) {this.owner=owner;data=new long[2*capacity];}
    void put(long key,long value) {
      check(data!=null && size*2<data.length,"Cache capacity exceeded");
      check(size==0||data[2*(size-1)]<key,"Cache keys not strictly increasing");
      data[2*size]=key;data[2*size+1]=value;size++;
    }
    long get(long key,Object token) {
      if(data==null||owner!=token) throw new IllegalStateException("Invalid workflow cache lifetime");
      int lo=0,hi=size-1;
      while(lo<=hi) {int m=(lo+hi)>>>1;if(data[2*m]==key)return data[2*m+1];if(data[2*m]<key)lo=m+1;else hi=m-1;}
      return 0; // Only called for rows of the exact predeclared demand union.
    }
    long ownedBytes() {return MemoryAgent.size(this)+(data==null?0:MemoryAgent.size(data));}
    public void close() {if(data!=null){Arrays.fill(data,0);data=null;}size=0;}
  }
  static long fixedBytes;
  static long requiredBytes(Work w) {return fixedBytes+16L*(w.a.limit+w.b.limit);}
  static String page(Page p) {return "SELECT row_id,k,label FROM targets WHERE tenant="+p.tenant+" AND visible ORDER BY row_id LIMIT "+p.limit+" OFFSET "+p.offset;}
  static String effective(int tenant) {return "l.tenant="+tenant+" AND NOT l.deleted AND l.doc IN (SELECT doc FROM allowed WHERE tenant="+tenant+")";}
  static String single(Page p,boolean grouped) {
    String base=" FROM ("+page(p)+") w ";
    return grouped?"SELECT w.row_id,w.k,w.label,count(DISTINCT l.doc) AS document_count"+base
      +"LEFT JOIN links l ON l.k=w.k AND "+effective(p.tenant)+" GROUP BY w.row_id,w.k,w.label ORDER BY w.row_id":
      "SELECT w.row_id,w.k,w.label,(SELECT count(DISTINCT l.doc) FROM links l WHERE l.k=w.k AND "+effective(p.tenant)+") AS document_count"+base+"ORDER BY w.row_id";
  }
  static String prefix(Work w,boolean badDomain) {
    return "WITH wa AS MATERIALIZED ("+page(w.a)+"),wb AS MATERIALIZED ("+page(w.b)+"), "
      +"keys AS (SELECT k FROM wa"+(badDomain?"":" UNION SELECT k FROM wb")+"), "
      +"g AS MATERIALIZED (SELECT l.k,count(DISTINCT l.doc) AS n FROM links l WHERE "+effective(w.a.tenant)
      +" AND l.k IN (SELECT k FROM keys) GROUP BY l.k) ";
  }
  static String shared(Work w,boolean badDomain) {
    return prefix(w,badDomain)+"SELECT 0 AS consumer,w.row_id,w.k,w.label,COALESCE(g.n,0::bigint) AS document_count FROM wa w LEFT JOIN g ON g.k=w.k "
      +"UNION ALL SELECT 1 AS consumer,w.row_id,w.k,w.label,COALESCE(g.n,0::bigint) AS document_count FROM wb w LEFT JOIN g ON g.k=w.k ORDER BY consumer,row_id";
  }
  static String pages(Work w) {
    return "WITH wa AS ("+page(w.a)+"),wb AS ("+page(w.b)+") SELECT 0 AS consumer,* FROM wa UNION ALL SELECT 1 AS consumer,* FROM wb ORDER BY consumer,row_id";
  }
  static boolean eligible(Connection c,Work w) throws Exception {
    return w.a.tenant==w.b.tenant && c.isReadOnly() && !c.getAutoCommit() && c.getTransactionIsolation()==Connection.TRANSACTION_SERIALIZABLE;
  }
  static List<String> queries(Work w,String arm) {
    return switch(arm) {
      case "independent_correlated" -> List.of(single(w.a,false),single(w.b,false));
      case "independent_grouped" -> List.of(single(w.a,true),single(w.b,true));
      case "db_shared" -> List.of(shared(w,false));
      case "app_shared" -> List.of(prefix(w,false)+"SELECT k,n FROM g ORDER BY k",pages(w));
      default -> throw new IllegalArgumentException(arm);
    };
  }
  static JSONArray split(JSONArray rows) {
    JSONArray result=new JSONArray().put(new JSONArray()).put(new JSONArray());
    for(Object item:rows) {
      JSONArray in=(JSONArray)item,out=new JSONArray();int consumer=in.getJSONObject(0).getInt("value");
      for(int i=1;i<in.length();i++)out.put(in.get(i));result.getJSONArray(consumer).put(out);
    }
    return result;
  }
  static Run execute(Connection c,Work w,String requested,long budget) throws Exception {
    boolean legal=eligible(c,w);String arm=requested;
    if((arm.equals("app_shared")||arm.equals("db_shared")) && !legal)arm="independent_correlated";
    if(arm.equals("app_shared") && requiredBytes(w)>budget)arm="independent_correlated";
    List<String> sql=queries(w,arm);JSONArray result;long bytes=0;int entries=0;
    if(arm.startsWith("independent"))result=new JSONArray().put(DatabaseSmoke.rows(c,sql.get(0))).put(DatabaseSmoke.rows(c,sql.get(1)));
    else if(arm.equals("db_shared"))result=split(DatabaseSmoke.rows(c,sql.get(0)));
    else {
      Object owner=new Object();Cache cache=new Cache(w.a.limit+w.b.limit,owner);
      try {
        bytes=cache.ownedBytes();check(bytes==requiredBytes(w)&&bytes<=budget,"Cache accounting mismatch");
        try(Statement s=c.createStatement();ResultSet r=s.executeQuery(sql.get(0))) {
          check(r.getMetaData().getColumnType(1)==Types.INTEGER&&r.getMetaData().getColumnType(2)==Types.BIGINT,"Cache value types");
          while(r.next())cache.put(r.getInt(1),r.getLong(2));
        }
        entries=cache.size;JSONArray target=DatabaseSmoke.rows(c,sql.get(1));
        for(Object item:target) {
          JSONArray row=(JSONArray)item;long value=cache.get(row.getJSONObject(2).getInt("value"),owner);
          row.put(new JSONObject().put("value",value).put("java_type","java.lang.Long").put("jdbc_type",Types.BIGINT));
        }
        result=split(target);
      } finally {cache.close();check(cache.data==null,"Cache retained across workflow");}
    }
    return new Run(result.toString(),arm,bytes,entries);
  }
  static JSONArray oracle(Connection c,Work w) throws Exception {
    // Existing raw-row oracle uses tenant 1. Context mismatch has a separate SQL baseline check.
    return new JSONArray().put(DatabaseSmoke.pageOracle(c,w.a.offset,w.a.limit)).put(DatabaseSmoke.pageOracle(c,w.b.offset,w.b.limit));
  }
  static JSONArray values(String response) {
    JSONArray result=new JSONArray();for(Object page:new JSONArray(response))result.put(DatabaseSmoke.values((JSONArray)page));return result;
  }
  static void record(String name,JSONObject detail) {semantics.put(detail.put("case",name).put("status","passed"));}
  static void semanticTests() throws Exception {
    int[][] offsets={{0,2},{0,1},{0,0},{99,99}};
    for(String fixture:List.of("empty","no_links","mixed")) {
      DatabaseSmoke.pageFixture(fixture);
      for(int[] o:offsets) try(Connection c=DatabaseCosts.connect(url)) {
        Work w=new Work(new Page(o[0],2,1),new Page(o[1],2,1));String before=DatabaseCosts.hash(DatabaseSmoke.state(c));
        Run base=execute(c,w,"independent_correlated",4096);check(values(base.response).similar(oracle(c,w)),"Workflow oracle mismatch");
        JSONObject routes=new JSONObject();
        for(String arm:direct) {
          Run result=execute(c,w,arm,4096);check(result.response.equals(base.response),"Semantic mismatch: "+arm);
          routes.put(arm,new JSONObject().put("route",result.route).put("cache_owned_bytes",result.cacheBytes).put("entries",result.entries));
        }
        Run fallback=execute(c,w,"app_shared",0);check(fallback.route.equals("independent_correlated")&&fallback.cacheBytes==0&&fallback.response.equals(base.response),"Zero-budget fallback");
        check(before.equals(DatabaseCosts.hash(DatabaseSmoke.state(c))),"State mutated");
        record("four_plans_"+fixture+"_"+Arrays.toString(o),new JSONObject().put("expected",new JSONArray(base.response)).put("routes",routes).put("zero_budget_fallback",true).put("state_unchanged",true));c.commit();
      }
    }
    DatabaseSmoke.pageFixture("mixed");
    Work w=new Work(new Page(0,1,1),new Page(3,1,1));
    try(Connection c=DatabaseCosts.connect(url)) {
      Run base=execute(c,w,"independent_correlated",4096);long required=requiredBytes(w);
      Run exact=execute(c,w,"app_shared",required),below=execute(c,w,"app_shared",required-1);
      check(exact.route.equals("app_shared")&&exact.cacheBytes==required&&below.cacheBytes==0&&below.route.equals("independent_correlated")&&exact.response.equals(base.response)&&below.response.equals(base.response),"Exact budget boundary");
      record("exact_budget_boundary",new JSONObject().put("required_bytes",required).put("below_route",below.route).put("exact_route",exact.route));
      Work different=new Work(w.a,new Page(0,1,2));Run expected=execute(c,different,"independent_correlated",4096);
      for(String arm:List.of("app_shared","db_shared")) {
        Run result=execute(c,different,arm,4096);check(result.route.equals("independent_correlated")&&result.response.equals(expected.response),"Context mismatch shared");
      }
      record("tenant_context_mismatch",new JSONObject().put("expected",new JSONArray(expected.response)).put("shared_routes","independent_correlated"));
      String wrong=split(DatabaseSmoke.rows(c,shared(w,true))).toString();check(!wrong.equals(base.response),"Insufficient union mutant survived");
      record("insufficient_shared_domain_mutant",new JSONObject().put("original",new JSONArray(base.response)).put("wrong",new JSONArray(wrong)));c.commit();
    }
    Object owner=new Object();Cache cache=new Cache(1,owner);cache.put(10,2);boolean wrongOwner=false,closed=false;
    try {cache.get(10,new Object());} catch(IllegalStateException expected){wrongOwner=true;}
    cache.close();try {cache.get(10,owner);}catch(IllegalStateException expected){closed=true;}
    check(wrongOwner&&closed&&cache.data==null,"Lifetime guard");
    record("cache_lifetime",new JSONObject().put("wrong_owner_rejected",true).put("closed_rejected",true));
    Work one=new Work(new Page(0,1,1),new Page(0,1,1));String previous;
    try(Connection c=DatabaseCosts.connect(url)){previous=execute(c,one,"app_shared",4096).response;c.commit();}
    try(Connection c=DatabaseSmoke.connect(false)){DatabaseSmoke.sql(c,"DELETE FROM allowed WHERE tenant=1 AND doc=2");c.commit();}
    try(Connection c=DatabaseCosts.connect(url)) {
      String fresh=execute(c,one,"app_shared",4096).response;
      check(!fresh.equals(previous)&&fresh.equals(execute(c,one,"independent_correlated",4096).response),"Result retained across workflows");
      record("permission_change_between_workflows",new JSONObject().put("before",new JSONArray(previous)).put("after",new JSONArray(fresh)));c.commit();
    }
    check(semantics.length()==17,"Semantic case count");
  }
  static JSONObject features(Connection c,Work w,String arm) throws Exception {
    double cost=0,bytes=0;JSONArray estimates=new JSONArray();List<String> sql=queries(w,arm);
    for(String query:sql) try(Statement s=c.createStatement();ResultSet r=s.executeQuery("EXPLAIN (FORMAT JSON) "+query)) {
      check(r.next(),"Missing plan");JSONObject plan=new JSONArray(r.getString(1)).getJSONObject(0).getJSONObject("Plan");
      cost+=plan.getDouble("Total Cost");bytes+=plan.getDouble("Plan Rows")*plan.getDouble("Plan Width");
      estimates.put(new JSONObject().put("sql",query).put("plan",plan));
    }
    return new JSONObject().put("vector",new JSONArray().put(sql.size()).put(cost).put(bytes).put(arm.equals("app_shared")?requiredBytes(w):0)).put("estimates",estimates);
  }
  static JSONObject select(Connection c,Work w,long budget) throws Exception {
    String best=null;double minimum=Double.POSITIVE_INFINITY;JSONObject predicted=new JSONObject();
    boolean legal=eligible(c,w);
    for(String arm:direct) {
      if((arm.equals("app_shared")||arm.equals("db_shared"))&&!legal)continue;
      if(arm.equals("app_shared")&&requiredBytes(w)>budget)continue;
      JSONArray vector=features(c,w,arm).getJSONArray("vector");double value=0;
      for(int i=0;i<vector.length();i++) value+=model.getJSONArray("weights").getDouble(i)*vector.getDouble(i)/model.getJSONArray("scales").getDouble(i);
      check(Double.isFinite(value)&&value>=0,"Invalid prediction");predicted.put(arm,value);
      if(value<minimum){minimum=value;best=arm;}
    }
    check(best!=null,"No feasible plan");return new JSONObject().put("selected",best).put("predicted_ms",predicted);
  }
  static void testCase(String stage,int seed,int pageSize,int overlap) throws Exception {
    DatabaseCosts.fixture(protocol.getInt("targets"),protocol.getInt("effective_links_per_target"),seed);
    Work w=new Work(new Page(0,pageSize,1),new Page(pageSize*(100-overlap)/100,pageSize,1));
    JSONObject entry=new JSONObject().put("seed",seed).put("page_size",pageSize).put("overlap_percent",overlap)
      .put("cache_budget_bytes",protocol.getLong("cache_owned_budget_bytes")).put("required_cache_bytes",requiredBytes(w));
    cases.put(entry);JSONArray samples=new JSONArray();entry.put("samples",samples);
    List<String> arms=new ArrayList<>(direct);if(stage.equals("evaluate"))arms.add("selector");
    try(Connection c=DatabaseCosts.connect(url)) {
      String before=DatabaseCosts.hash(DatabaseSmoke.state(c));Run base=execute(c,w,"independent_correlated",4096);
      check(values(base.response).similar(oracle(c,w)),"Performance oracle");
      JSONObject estimates=new JSONObject();for(String arm:direct) {
        check(execute(c,w,arm,4096).response.equals(base.response),"Preflight equality");estimates.put(arm,features(c,w,arm));
      }
      entry.put("features",estimates).put("expected_typed_response",new JSONArray(base.response)).put("state_sha256",before);
      for(int warm=0;warm<2;warm++)for(String arm:arms) {
        String selected=arm.equals("selector")?select(c,w,4096).getString("selected"):arm;
        check(execute(c,w,selected,4096).response.equals(base.response),"Warmup equality");
      }
      for(int block=0;block<protocol.getInt("blocks");block++) {
        List<String> schedule=new ArrayList<>(arms);Collections.shuffle(schedule,order);
        for(int position=0;position<schedule.size();position++) {
          String arm=schedule.get(position);long start=System.nanoTime();
          JSONObject selection=arm.equals("selector")?select(c,w,4096):null;
          long planning=System.nanoTime()-start;
          Run result=execute(c,w,selection==null?arm:selection.getString("selected"),4096);
          long elapsed=System.nanoTime()-start;
          check(result.response.equals(base.response),"Timed complete result mismatch");
          JSONObject sample=new JSONObject().put("arm",arm).put("route",result.route).put("block",block).put("position",position)
            .put("elapsed_ns",elapsed).put("selection_ns",selection==null?0:planning).put("cache_owned_bytes",result.cacheBytes)
            .put("cache_entries",result.entries).put("cache_released",true).put("output_verified",true);
          if(selection!=null)sample.put("selection",selection);samples.put(sample);
        }
      }
      check(before.equals(DatabaseCosts.hash(DatabaseSmoke.state(c))),"Workflow changed DB state");entry.put("state_unchanged",true);c.commit();
    }
    Files.writeString(output,report.toString(2)+"\n");System.out.println(stage+" seed="+seed+" page="+pageSize+" overlap="+overlap);System.out.flush();
  }
  public static void main(String[] args) throws Exception {
    String stage=args[0];url=args[1];output=Path.of(args[2]);protocol=new JSONObject(Files.readString(Path.of(args[3])));
    check(stage.equals("calibrate")||stage.equals("evaluate"),"Unknown stage");
    if(stage.equals("evaluate"))model=new JSONObject(Files.readString(Path.of(args[4])));
    for(Object arm:protocol.getJSONArray("direct_arms"))direct.add((String)arm);
    order=new Random(protocol.getLong("arm_order_seed"));DatabaseSmoke.url=url;
    Object owner=new Object();Cache empty=new Cache(0,owner);fixedBytes=empty.ownedBytes();empty.close();
    for(int capacity:List.of(1,2,20,200))try(Cache probe=new Cache(capacity,owner)){check(probe.ownedBytes()==fixedBytes+16L*capacity,"Unsupported array layout");}
    report.put("status","running").put("stage",stage).put("protocol",protocol).put("cases",cases).put("semantic_checks",semantics)
      .put("cache_fixed_owned_bytes",fixedBytes).put("cache_bytes_per_capacity_entry",16);
    if(model!=null)report.put("model_sha256",DatabaseCosts.hash(Files.readString(Path.of(args[4]))));
    try {
      if(stage.equals("calibrate")) {
        DatabaseSmoke.schema();report.put("environment",DatabaseSmoke.report);semanticTests();
        try(Connection c=DatabaseSmoke.connect(false)) {
          DatabaseSmoke.sql(c,"CREATE INDEX targets_context_order ON targets(tenant,visible,row_id) INCLUDE(k,label)");
          DatabaseSmoke.sql(c,"CREATE INDEX links_context_key_doc ON links(tenant,k,doc) INCLUDE(deleted)");
          DatabaseSmoke.sql(c,"CREATE INDEX allowed_context_doc ON allowed(tenant,doc)");c.commit();
        }
      }
      for(Object seed:protocol.getJSONArray(stage.equals("calibrate")?"calibration_seeds":"evaluation_seeds"))
        for(Object pageSize:protocol.getJSONArray("page_sizes"))
          for(Object overlap:protocol.getJSONArray("overlap_percentages"))testCase(stage,(Integer)seed,(Integer)pageSize,(Integer)overlap);
      int count=0;for(Object c:cases)count+=((JSONObject)c).getJSONArray("samples").length();
      check(count==protocol.getInt(stage.equals("calibrate")?"expected_calibration_samples":"expected_evaluation_samples"),"Sample count");
      report.put("timed_samples",count).put("status","passed");
    } catch(Throwable error){report.put("status","failed").put("failure",error.toString());throw error;}
    finally{Files.writeString(output,report.toString(2)+"\n");}
  }
}
