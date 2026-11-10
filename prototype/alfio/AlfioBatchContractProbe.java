package research.alfio;

import alfio.model.TicketCategory;
import alfio.model.PriceContainer.VatStatus;
import alfio.model.modification.AttendeeData;
import alfio.repository.TicketRepository;
import alfio.util.Json;
import ch.digitalfondue.npjt.Query;
import java.lang.reflect.*;
import java.nio.file.*;
import java.security.MessageDigest;
import java.sql.*;
import java.util.*;
import java.util.function.IntFunction;
import javax.sql.DataSource;
import org.springframework.dao.DataIntegrityViolationException;
import org.springframework.jdbc.core.namedparam.*;
import org.springframework.jdbc.datasource.AbstractDataSource;

/** Characterizes the unchanged binary method, not an optimized implementation. */
public class AlfioBatchContractProbe {
  static Path out;
  static final List<Map<String,Object>> checks = new ArrayList<>();
  static void check(boolean condition, String label) {
    checks.add(Map.of("check",label,"passed",condition));
    if (!condition) throw new AssertionError(label);
  }
  static void save(String file,Object value) throws Exception { Files.writeString(out.resolve(file),Json.toJson(value)); }
  static String query() throws Exception { return TicketRepository.class.getMethod("batchReserveTickets").getAnnotation(Query.class).value(); }
  static TicketCategory category(int id) {
    return new TicketCategory(id,null,null,20,"contract",false,TicketCategory.Status.ACTIVE,1,true,
      1234,null,null,null,null,null,"EUR",0,TicketCategory.TicketCheckInStrategy.ONCE_PER_EVENT,TicketCategory.TicketAccessType.INHERIT);
  }
  static AttendeeData attendee(String first,String last,String email,Map<String,String> metadata) {
    return new AttendeeData(first,last,email,"ZH","external-reference-kept-outside-this-method",metadata,Map.of("field",List.of("value")));
  }
  static Map<String,String> metadataInput() {
    // Map.of iteration order varies between JVMs. Fix input order, not observed output.
    Map<String,String> data=new LinkedHashMap<>();data.put("unicode","值🙂");data.put("quote","\"\\\n");return data;
  }
  static Object invoke(Object target,Method method,Object[] args) throws Throwable {
    try { return method.invoke(target,args); } catch (InvocationTargetException e) { throw e.getCause(); }
  }
  static TicketRepository repository(Capture template) {
    return (TicketRepository)Proxy.newProxyInstance(TicketRepository.class.getClassLoader(),new Class<?>[]{TicketRepository.class},(proxy,method,args)->{
      if (method.isDefault()) return InvocationHandler.invokeDefault(proxy,method,args);
      if (method.getName().equals("getNamedParameterJdbcTemplate")) return template;
      if (method.getName().equals("batchReserveTickets")) return query();
      throw new UnsupportedOperationException(method.toString());
    });
  }
  static class Capture extends NamedParameterJdbcTemplate {
    int calls; int[] returned = {}; int[] actual; SqlParameterSource[] params; String sql;
    RuntimeException failure; boolean execute;
    Capture(DataSource ds,boolean execute) { super(ds); this.execute=execute; }
    @Override public int[] batchUpdate(String sql,SqlParameterSource[] params) {
      calls++; this.sql=sql; this.params=params;
      if (failure!=null) throw failure;
      actual=execute?super.batchUpdate(sql,params):returned;
      return actual;
    }
  }
  static class Wire extends AbstractDataSource {
    final Connection connection; final List<Object> events = new ArrayList<>();
    Wire(Connection c) { connection=c; }
    @Override public Connection getConnection() {
      if(connection==null) throw new AssertionError("offline capture must not connect");
      return (Connection)Proxy.newProxyInstance(getClass().getClassLoader(),new Class<?>[]{Connection.class},(p,m,a)->{
        if (m.getName().equals("close")) return null;
        Object value=invoke(connection,m,a);
        if (m.getName().equals("prepareStatement")) {
          events.add(Map.of("prepareStatement",a[0]));
          PreparedStatement statement=(PreparedStatement)value;
          return Proxy.newProxyInstance(getClass().getClassLoader(),new Class<?>[]{PreparedStatement.class},(pp,mm,aa)->{
            String name=mm.getName();
            if(name.startsWith("set")||name.equals("addBatch")||name.equals("executeBatch"))
              events.add(Map.of("method",name,"args",aa==null?List.of():Arrays.asList(aa)));
            return invoke(statement,mm,aa);
          });
        }
        return value;
      });
    }
    @Override public Connection getConnection(String user,String password) { return getConnection(); }
  }
  static List<Object> bindings(Capture c) {
    List<Object> rows=new ArrayList<>();
    if(c.params==null) return rows;
    for(SqlParameterSource p:c.params) {
      Map<String,Object> row=new TreeMap<>();
      for(String name:p.getParameterNames()) {
        Object value=p.getValue(name); Map<String,Object> entry=new LinkedHashMap<>();
        entry.put("value",value); entry.put("javaType",value==null?null:value.getClass().getName()); entry.put("sqlType",p.getSqlType(name));
        row.put(name,entry);
      }
      rows.add(row);
    }
    return rows;
  }
  static void offline() throws Exception {
    Capture c=new Capture(new Wire(null),false); c.returned=new int[]{1,1,0,1};
    List<Integer> order=new ArrayList<>();
    List<AttendeeData> data=Arrays.asList(null,attendee("",null,"",Map.of()),
      attendee("张","O'Neil","Case@Example.test",metadataInput()),attendee(null,"last","",null));
    int result=repository(c).reserveTickets("reservation",Arrays.asList(11,11,null,13),category(7),"EN",VatStatus.INCLUDED,i->{order.add(i);return data.get(i);});
    check(result==3 && order.equals(List.of(0,1,2,3)) && c.calls==1,"ordered supplier and one batch dispatch, duplicates and null ID retained");
    check(c.params[0].getValue("ticketMetadata").equals("{}"),"null attendee produces absent metadata and null contact fields");
    check(!c.params[1].getValue("ticketMetadata").equals("{}"),"empty metadata map differs from absent metadata");
    check(c.params[0].getValue("email")==null && c.params[1].getValue("email").equals(""),"NULL and empty contact values remain distinct");
    check(c.params[2].getValue("userLanguage").equals("EN"),"batch language comes from method argument without attendee-language normalization");
    check(c.params[2].getValue("firstName").equals("张") && c.params[2].getValue("email").equals("Case@Example.test"),"contact strings preserved");
    Map metadata=Json.fromJson((String)c.params[2].getValue("ticketMetadata"),Map.class);
    Map attributes=(Map)((Map)((Map)metadata.get("metadataMap")).get("general")).get("attributes");
    check(attributes.equals(data.get(2).getMetadata()),"complete metadata survives original Java envelope encoding");
    save("binding-abi.json",Map.of("sql",c.sql,"rows",bindings(c),"supplierOrder",order,"result",result));
    Capture empty=new Capture(new Wire(null),false);
    check(repository(empty).reserveTickets(null,List.of(),null,null,null,i->{throw new AssertionError();})==0 && empty.calls==1,"empty input does not evaluate category, VAT or supplier; dispatches empty batch");
    List<Object> arithmetic=new ArrayList<>();
    for(int[] counts:new int[][]{{},{1,0,1},{Statement.SUCCESS_NO_INFO},{Statement.EXECUTE_FAILED},{Integer.MAX_VALUE,Integer.MAX_VALUE}}) {
      Capture a=new Capture(new Wire(null),false); a.returned=counts;
      int got=repository(a).reserveTickets("r",List.of(1),category(7),null,VatStatus.NONE,i->null);
      int expected=(int)Arrays.stream(counts).asLongStream().sum();
      check(got==expected,"injected update-count fold "+Arrays.toString(counts));
      arithmetic.add(Map.of("syntheticCounts",counts,"result",got,"scope","method fold only; not claimed driver-reachable"));
    }
    save("synthetic-count-fold.json",arithmetic);
    Capture before=new Capture(new Wire(null),false); List<Integer> evaluated=new ArrayList<>(); RuntimeException sentinel=new IllegalArgumentException("supplier sentinel");
    try { repository(before).reserveTickets("r",List.of(1,2,3),category(7),null,VatStatus.NONE,i->{evaluated.add(i);if(i==1)throw sentinel;return null;}); throw new AssertionError(); }
    catch(IllegalArgumentException e) { check(e==sentinel && before.calls==0 && evaluated.equals(List.of(0,1)),"all Java parameter materialization precedes first database dispatch"); }
    Capture badVat=new Capture(new Wire(null),false);
    try { repository(badVat).reserveTickets("r",List.of(1),category(7),null,null,i->null); throw new AssertionError(); }
    catch(NullPointerException e) { check(badVat.calls==0,"null VAT on nonempty input fails before database dispatch"); }
    Capture badName=new Capture(new Wire(null),false);
    try { repository(badName).reserveTickets("r",List.of(1,2),category(7),null,VatStatus.NONE,i->i==0?null:attendee("","","",Map.of())); throw new AssertionError(); }
    catch(IllegalArgumentException e) { check(badName.calls==0,"two empty nonnull names fail CustomerName validation before every write");save("name-error.json",Map.of("class",e.getClass().getName(),"message",e.getMessage(),"batchCalls",badName.calls)); }
    Capture badJson=new Capture(new Wire(null),false); Map<String,String> invalid=new HashMap<>(); invalid.put(null,"bad key");
    try { repository(badJson).reserveTickets("r",List.of(1,2),category(7),null,VatStatus.NONE,i->i==0?null:attendee(null,null,null,invalid)); throw new AssertionError(); }
    catch(RuntimeException e) { check(badJson.calls==0,"metadata serialization failure precedes all database dispatch"); save("serialization-error.json",Map.of("class",e.getClass().getName(),"batchCalls",badJson.calls)); }
    Capture error=new Capture(new Wire(null),false); error.failure=new DataIntegrityViolationException("template sentinel");
    try { repository(error).reserveTickets("r",List.of(1),category(7),null,VatStatus.NONE,i->null);throw new AssertionError(); }
    catch(DataIntegrityViolationException e) { check(e==error.failure,"repository propagates template exception without replacement"); }
  }
  static List<Map<String,Object>> rows(Connection c,String sql) throws Exception {
    try(Statement st=c.createStatement();ResultSet rs=st.executeQuery(sql)) {
      List<Map<String,Object>> result=new ArrayList<>();
      while(rs.next()) { Map<String,Object> row=new LinkedHashMap<>(); for(int i=1;i<=rs.getMetaData().getColumnCount();i++)row.put(rs.getMetaData().getColumnLabel(i),rs.getObject(i));result.add(row); }
      return result;
    }
  }
  static void exec(Connection c,String sql) throws Exception { try(Statement s=c.createStatement()){s.execute(sql);} }
  static String quote(String name) { return "\""+name.replace("\"","\"\"")+"\""; }
  static Map<String,Object> snapshot(Connection c) throws Exception {
    Map<String,Object> result=new TreeMap<>();
    for(var row:rows(c,"select tablename from pg_tables where schemaname='public' order by tablename")) {
      String name=(String)row.get("tablename");
      String value=(String)rows(c,"select coalesce(jsonb_agg(to_jsonb(t) order by to_jsonb(t)::text),'[]')::text as value from public."+quote(name)+" t").get(0).get("value");
      result.put(name,HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(value.getBytes(java.nio.charset.StandardCharsets.UTF_8))));
    }
    for(var row:rows(c,"select sequencename from pg_sequences where schemaname='public' order by sequencename")) {
      String name=(String)row.get("sequencename");result.put("sequence:"+name,rows(c,"select last_value,is_called from public."+quote(name)));
    }
    return result;
  }
  static long witness(Connection c) throws Exception {
    var row=rows(c,"select last_value,is_called from pg_temp.effect_seq").get(0);
    return Boolean.TRUE.equals(row.get("is_called"))?((Number)row.get("last_value")).longValue():0;
  }
  static void database(String url) throws Exception {
    try(Connection c=DriverManager.getConnection(url,"alfio_user",System.getenv("ALFIO_DB_PASSWORD"))) {
      var role=rows(c,"select current_user,rolsuper,rolbypassrls from pg_roles where rolname=current_user");
      check(Boolean.FALSE.equals(role.get(0).get("rolsuper"))&&Boolean.FALSE.equals(role.get(0).get("rolbypassrls")),"contract uses non-superuser without BYPASSRLS");save("role.json",role);
      save("schema-effects.json",Map.of(
        "triggers",rows(c,"select tgname,pg_get_triggerdef(oid) definition from pg_trigger where tgrelid='public.ticket'::regclass order by tgname"),
        "constraints",rows(c,"select conname,pg_get_constraintdef(oid) definition from pg_constraint where conrelid='public.ticket'::regclass order by conname"),
        "policies",rows(c,"select policyname,qual,with_check from pg_policies where schemaname='public' and tablename='ticket'"),
        "rls",rows(c,"select relrowsecurity,relforcerowsecurity from pg_class where oid='public.ticket'::regclass"),
        "accessFunction",rows(c,"select pg_get_functiondef('public.alfio_check_row_access(integer)'::regprocedure) definition")));
      var tickets=rows(c,"select id,category_id,organization_id_fk from public.ticket order by id limit 2");
      int first=((Number)tickets.get(0).get("id")).intValue(),second=((Number)tickets.get(1).get("id")).intValue();
      int categoryId=((Number)tickets.get(0).get("category_id")).intValue(); String org=tickets.get(0).get("organization_id_fk").toString();
      int missing=((Number)rows(c,"select max(id)+1 as id from public.ticket").get(0).get("id")).intValue();
      Map<String,Object> initial=snapshot(c); save("initial-state.json",initial);
      // A synthetic observation trigger records UPDATE order. Its sequence deliberately survives rollback.
      // This is a witness for effect ordering, not a claim about existing production trigger behavior.
      exec(c,"create temporary sequence effect_seq");
      exec(c,"create temporary table effect_log(ord bigint, ticket_id integer, email text)");
      exec(c,"create function pg_temp.observe_ticket() returns trigger language plpgsql security invoker as $$ begin insert into pg_temp.effect_log values(nextval('pg_temp.effect_seq'),new.id,new.email_address); return new; end $$");
      exec(c,"create trigger rdb_contract_observer after update on public.ticket for each row execute function pg_temp.observe_ticket()");
      List<Object> results=new ArrayList<>();
      for(String test:List.of("ordered-duplicates","missing-id","null-id","empty","rls-denied","supplier-error","length-error-second","foreign-key-error")) {
        c.setAutoCommit(false); long before=witness(c); Wire wire=new Wire(c); Capture capture=new Capture(wire,true);
        Map<String,Object> record=new LinkedHashMap<>();record.put("case",test);
        exec(c,"select set_config('alfio.checkRowAccess','true',true),set_config('alfio.currentUserOrgs','"+(test.equals("rls-denied")?"":org)+"',true)");
        List<Integer> ids=switch(test) {
          case "ordered-duplicates" -> List.of(first,second,first);
          case "missing-id" -> List.of(first,missing,second);
          case "null-id" -> Arrays.asList((Integer)null);
          case "empty" -> List.of();
          default -> List.of(first,second);
        };
        String reservation=test.equals("foreign-key-error")?"00000000-0000-4000-8000-000000000001":null;
        IntFunction<AttendeeData> supplier=i->{
          if(test.equals("supplier-error")&&i==1)throw new IllegalArgumentException("supplier sentinel");
          if(test.equals("length-error-second")&&i==1)return attendee("ok","ok","x".repeat(256),null);
          if(i==1)return attendee("",null,"",Map.of());
          if(i==2)return null;
          return attendee("张","O'Neil","Case@Example.test",metadataInput());
        };
        boolean expectError=Set.of("supplier-error","length-error-second","foreign-key-error").contains(test);
        try {
          int count=repository(capture).reserveTickets(reservation,ids,category(categoryId),null,VatStatus.INCLUDED,supplier);
          check(!expectError,test+": expected success");
          int expected=switch(test){case "ordered-duplicates"->3;case "missing-id"->2;default->0;};
          check(count==expected,test+": exact affected-row aggregate");
          record.put("count",count);record.put("driverCounts",capture.actual);
          var effects=rows(c,"select ord,ticket_id,email from pg_temp.effect_log order by ord");record.put("effects",effects);
          check(effects.size()==expected,test+": one observable row effect per successful match");
          if(test.equals("ordered-duplicates")) {
            check(effects.stream().map(r->((Number)r.get("ticket_id")).intValue()).toList().equals(ids),"duplicate ticket update order retained");
            var values=rows(c,"select id,first_name,last_name,email_address,user_language,metadata::text from public.ticket where id in ("+first+","+second+") order by id");
            check(values.get(0).get("email_address")==null && values.get(1).get("email_address").equals(""),"database NULL versus empty and last duplicate wins");
            check(values.get(0).get("metadata").equals("{}")&&!values.get(1).get("metadata").equals("{}"),"database absent and empty-map metadata remain distinct");
            record.put("writtenRows",values);
          }
        } catch(RuntimeException e) {
          check(expectError,test+": expected failure"); record.put("exceptionClass",e.getClass().getName());
          SQLException sqlError=null; BatchUpdateException batch=null;
          for(Throwable cause=e;cause!=null;cause=cause.getCause()) {
            if(cause instanceof SQLException se)sqlError=se;
            if(cause instanceof BatchUpdateException be)batch=be;
          }
          record.put("sqlState",sqlError==null?null:sqlError.getSQLState());record.put("batchExceptionCounts",batch==null?null:batch.getUpdateCounts());
          if(test.equals("supplier-error"))check(e instanceof IllegalArgumentException&&capture.calls==0,"supplier error performs no batch call");
          else {
            check(e instanceof DataIntegrityViolationException,"Spring translates SQL integrity/data error");
            check(sqlError!=null&&sqlError.getSQLState().equals(test.equals("length-error-second")?"22001":"23503"),test+": expected SQLSTATE");
            try { rows(c,"select 1");throw new AssertionError("failed transaction accepted query"); }
            catch(SQLException aborted) { check(aborted.getSQLState().equals("25P02"),test+": transaction remains aborted until rollback"); }
          }
        } finally { c.rollback();c.setAutoCommit(true); }
        long delta=witness(c)-before;record.put("syntheticEffectSequenceDelta",delta);
        if(test.equals("length-error-second"))check(delta==1,"first row effect occurs before second row failure and sequence effect survives rollback");
        if(test.equals("supplier-error"))check(delta==0,"Java preparation failure precedes every row effect");
        check(snapshot(c).equals(initial),test+": rollback preserves every public table and sequence");
        record.put("fullStateRestored",true);record.put("wire",wire.events);record.put("bindings",bindings(capture));results.add(record);
        save("database-cases.json",results);
      }
      exec(c,"drop trigger rdb_contract_observer on public.ticket");
      check(snapshot(c).equals(initial),"all public business data preserved after contract tests");
    }
  }
  public static void main(String[] args) throws Exception {
    out=Path.of(args[0]);Files.createDirectories(out);
    try {
      offline();
      if(args.length>1)database(args[1]);
      save("origin.json",Map.of("TicketRepository",TicketRepository.class.getProtectionDomain().getCodeSource().getLocation().toString(),"scope","unchanged default method invoked through test proxy; service transaction proxy not under test"));
    } finally { save("checks.json",checks); }
  }
}
