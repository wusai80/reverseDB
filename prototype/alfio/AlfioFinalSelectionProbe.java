package research.alfio;

import alfio.manager.TicketReservationManager;
import alfio.manager.support.reservation.NotEnoughTicketsException;
import alfio.model.*;
import alfio.model.modification.*;
import alfio.repository.*;
import alfio.config.support.*;
import alfio.util.Json;
import ch.digitalfondue.npjt.QueryFactory;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.datasource.*;
import org.springframework.transaction.PlatformTransactionManager;
import java.lang.reflect.*;
import java.nio.file.*;
import java.sql.*;
import java.util.*;

/** Final bounded local audit. Original manager methods and NPJT execute real SQL.
 * A sentinel stops a successful outer path only AFTER the third category read
 * and mapping, before initialization/writes. No transformed application runs. */
public final class AlfioFinalSelectionProbe {
  static String url;static Path out;static List<String> checks=new ArrayList<>();static List<Object> cases=new ArrayList<>();
  static void check(String name,boolean ok){if(!ok)throw new AssertionError(name);checks.add(name);}
  static void save(String name,Object obj)throws Exception{Files.writeString(out.resolve(name),Json.toJson(obj));}
  static Connection connect()throws Exception{
    Connection c=DriverManager.getConnection(url,"alfio_user","synthetic-final-selection-only");c.setAutoCommit(false);
    try(var s=c.createStatement()){s.execute("set statement_timeout='5s'");}c.commit();return c;
  }
  static Object invoke(Object o,Method m,Object... args)throws Throwable{
    try{return m.invoke(o,args);}catch(InvocationTargetException e){throw e.getCause();}
  }
  static final class StopAfterReread extends RuntimeException{}
  static class Session implements AutoCloseable {
    Connection c;JdbcTemplate jdbc;TicketCategoryRepository categories;TicketRepository tickets;Event event;
    TicketReservationManager manager;List<String> calls=new ArrayList<>();List<TicketCategory> reads=new ArrayList<>();
    List<Integer> selected=new ArrayList<>();int categoryCalls;Runnable beforeReread;boolean stop;
    Session(int isolation)throws Exception{
      c=connect();c.setTransactionIsolation(isolation);var ds=new SingleConnectionDataSource(c,true);jdbc=new JdbcTemplate(ds);
      var factories=List.of(new JSONColumnMapper.Factory(),new ArrayColumnMapper.Factory(),new EnumTypeColumnMapper.Factory());
      var converters=List.of(new JSONColumnMapper.Converter(),new ArrayColumnMapper.Converter(),new EnumTypeColumnMapper.Converter());
      categories=QueryFactory.from(TicketCategoryRepository.class,"PGSQL",ds,new ArrayList<>(factories),new ArrayList<>(converters));
      tickets=QueryFactory.from(TicketRepository.class,"PGSQL",ds,new ArrayList<>(factories),new ArrayList<>(converters));
      event=QueryFactory.from(EventRepository.class,"PGSQL",ds,new ArrayList<>(factories),new ArrayList<>(converters)).findById(1);c.commit();
      var cp=(TicketCategoryRepository)Proxy.newProxyInstance(categories.getClass().getClassLoader(),new Class<?>[]{TicketCategoryRepository.class},(p,m,a)->{
        calls.add("category:"+m.getName());categoryCalls++;
        if(categoryCalls==3 && beforeReread!=null)beforeReread.run();
        Object value=invoke(categories,m,a);reads.add((TicketCategory)value);
        if(stop && categoryCalls==3)throw new StopAfterReread();return value;
      });
      var tp=(TicketRepository)Proxy.newProxyInstance(tickets.getClass().getClassLoader(),new Class<?>[]{TicketRepository.class},(p,m,a)->{
        calls.add("ticket:"+m.getName());Object value=invoke(tickets,m,a);
        if(value instanceof List<?> list)selected=new ArrayList<>((List<Integer>)list);return value;
      });
      var ctor=TicketReservationManager.class.getConstructors()[0];Object[] args=new Object[ctor.getParameterCount()];
      for(int i=0;i<args.length;i++){
        Class<?> type=ctor.getParameterTypes()[i];
        if(type==TicketRepository.class)args[i]=tp;
        else if(type==TicketCategoryRepository.class)args[i]=cp;
        else if(type==PlatformTransactionManager.class)args[i]=new DataSourceTransactionManager(ds);
      }
      manager=(TicketReservationManager)ctor.newInstance(args);
      jdbc.execute("set local alfio.checkRowAccess='true'");jdbc.execute("set local alfio.currentUserOrgs='1'");
    }
    List<Integer> helper(int qty,List<Ticket.TicketStatus> statuses)throws Throwable{
      Method m=TicketReservationManager.class.getDeclaredMethod("reserveTickets",int.class,int.class,int.class,List.class);m.setAccessible(true);
      return (List<Integer>)invoke(manager,m,1,1,qty,statuses);
    }
    Throwable outer(int qty)throws Exception{
      var req=new TicketReservationModification();req.setAmount(qty);req.setTicketCategoryId(1);
      var request=new TicketReservationWithOptionalCodeModification(req,Optional.empty());
      Method m=TicketReservationManager.class.getDeclaredMethod("reserveTicketsForCategory",Event.class,String.class,
          TicketReservationWithOptionalCodeModification.class,Locale.class,boolean.class,PromoCodeDiscount.class,PromoCodeDiscount.class);
      m.setAccessible(true);stop=true;
      try{invoke(manager,m,event,"00000000-0000-0000-0000-000000000001",request,Locale.ENGLISH,false,null,null);return null;}
      catch(Throwable t){return t;}
    }
    @Override public void close()throws Exception{try{c.rollback();}finally{c.close();}}
  }
  static String lockAttempt(int id)throws Exception{
    try(var c=connect();var s=c.createStatement()){
      try{s.executeQuery("select id from ticket where id="+id+" for update nowait").close();c.rollback();return "acquired";}
      catch(SQLException e){c.rollback();return e.getSQLState();}
    }
  }
  static void change(Connection writer,String assignments){
    try(var st=writer.createStatement()){check("writer changes one category",st.executeUpdate("update ticket_category set "+assignments+" where id=1")==1);writer.commit();}
    catch(SQLException e){throw new RuntimeException(e);}
  }
  static Map<String,Object> snapshot()throws Exception{
    try(var c=connect()){
      var jdbc=new JdbcTemplate(new SingleConnectionDataSource(c,true));Map<String,Object> rows=new TreeMap<>();
      for(String table:jdbc.queryForList("select tablename from pg_tables where schemaname='public' order by tablename",String.class)){
        String quoted="\""+table.replace("\"","\"\"")+"\"";
        rows.put(table,jdbc.queryForObject("select coalesce(jsonb_agg(to_jsonb(t) order by to_jsonb(t)::text collate \"C\"),'[]'::jsonb)::text from public."+quoted+" t",String.class));
      }
      String seq=jdbc.queryForObject("select jsonb_agg(to_jsonb(t) order by sequencename)::text from (select schemaname,sequencename,last_value from pg_sequences where schemaname='public') t",String.class);
      c.rollback();return Map.of("tables",rows,"sequences",seq);
    }
  }
  public static void main(String[] args)throws Throwable{
    url=args[0];out=Path.of(args[1]);Map<String,Object> before=snapshot();save("before-full-state.json",before);
    check("full fixture includes 63 tables",((Map<?,?>)before.get("tables")).size()==63);
    try(var s=new Session(Connection.TRANSACTION_READ_COMMITTED)){
      save("runtime.json",s.jdbc.queryForMap("select current_user,current_setting('transaction_isolation') isolation,current_setting('default_transaction_isolation') default_isolation,current_setting('alfio.checkRowAccess') rls_flag,current_setting('alfio.currentUserOrgs') orgs"));
      check("non-superuser role",!s.jdbc.queryForObject("select usesuper from pg_user where usename=current_user",Boolean.class));
      var ids=s.helper(3,List.of(Ticket.TicketStatus.FREE));check("bounded selection ordered and complete",ids.equals(List.of(1,2,3)));
      check("original bounded helper executes two repository calls",s.calls.equals(List.of("category:getByIdAndActive","ticket:selectTicketInCategoryForUpdateSkipLocked")));
      cases.add(Map.of("case","bounded original helper","ids",ids,"calls",new ArrayList<>(s.calls)));
    }
    try(var s=new Session(Connection.TRANSACTION_READ_COMMITTED)){
      s.jdbc.update("update ticket_category set bounded=false where id=1");s.jdbc.update("update ticket set category_id=null where id<=3");
      var ids=s.helper(3,List.of(Ticket.TicketStatus.FREE));check("unbounded selects unallocated rows",ids.equals(List.of(1,2,3)));
      check("original unbounded query selected",s.calls.get(1).equals("ticket:selectNotAllocatedTicketsForUpdateSkipLocked"));
      cases.add(Map.of("case","unbounded original helper","ids",ids,"calls",new ArrayList<>(s.calls),"changes_rolled_back",true));
    }
    try(var s=new Session(Connection.TRANSACTION_READ_COMMITTED)){
      s.jdbc.update("update ticket set status='RELEASED' where id=1");s.jdbc.update("update ticket set status='PRE_RESERVED' where id=2");
      var ids=s.helper(2,List.of(Ticket.TicketStatus.RELEASED,Ticket.TicketStatus.PRE_RESERVED));check("waiting status list preserved",ids.equals(List.of(1,2)));
      cases.add(Map.of("case","waiting status filter","ids",ids,"changes_rolled_back",true));
    }
    try(var locker=connect();var st=locker.createStatement()){
      st.executeQuery("select id from ticket where id<=2 for update").close();
      try(var s=new Session(Connection.TRANSACTION_READ_COMMITTED)){
        var ids=s.helper(3,List.of(Ticket.TicketStatus.FREE));check("skip locked retains ID ordering",ids.equals(List.of(3,4,5)));
        check("selected rows really locked",lockAttempt(3).equals("55P03"));check("unselected row not spuriously locked",lockAttempt(6).equals("acquired"));
        cases.add(Map.of("case","competing locks","other_locked",List.of(1,2),"selected",ids,"selected_nowait","55P03"));
      }
      check("original rollback releases selected locks",lockAttempt(3).equals("acquired"));locker.rollback();
    }
    try(var locker=connect();var st=locker.createStatement()){
      st.executeQuery("select id from ticket where id<20 for update").close();
      try(var s=new Session(Connection.TRANSACTION_READ_COMMITTED)){
        Throwable error=s.outer(2);check("actual outer quantity guard throws original exception",error instanceof NotEnoughTicketsException);
        check("guard stops before final category reread",s.categoryCalls==2 && s.calls.size()==3 && s.selected.equals(List.of(20)));
        check("shortage still holds selected lock until caller ends transaction",lockAttempt(20).equals("55P03"));
        cases.add(Map.of("case","actual outer insufficient quantity","selected",s.selected,"calls",s.calls,"error",error.getClass().getName(),"reread_executed",false));
      }
      locker.rollback();check("shortage rollback releases locks",lockAttempt(20).equals("acquired"));
    }
    try(var s=new Session(Connection.TRANSACTION_READ_COMMITTED)){
      Throwable stop=s.outer(3);check("stable actual outer reaches post-reread sentinel",stop instanceof StopAfterReread && s.categoryCalls==3);
      check("stable full category models equal",Json.toJson(s.reads.get(1)).equals(Json.toJson(s.reads.get(2))));
      cases.add(Map.of("case","stable full-model control","calls",s.calls,"first_category",s.reads.get(1),"reread_category",s.reads.get(2),"selected",s.selected));
    }
    for(int isolation:new int[]{Connection.TRANSACTION_READ_COMMITTED,Connection.TRANSACTION_REPEATABLE_READ}){
      try(var writer=connect()){
        try(var s=new Session(isolation)){
          s.beforeReread=()->change(writer,"src_price_cts=2000,bounded=false");Throwable stop=s.outer(3);
          check("concurrent outer reaches complete mapped reread "+isolation,stop instanceof StopAfterReread && s.reads.size()==3);
          TicketCategory first=s.reads.get(1),last=s.reads.get(2);boolean equal=Json.toJson(first).equals(Json.toJson(last));
          check("first bounded decision governs locked selection "+isolation,first.isBounded() && s.selected.equals(List.of(1,2,3)));
          if(isolation==Connection.TRANSACTION_READ_COMMITTED)check("READ COMMITTED reread differs from reuse",!equal && first.getSrcPriceCts()==1000 && last.getSrcPriceCts()==2000 && !last.isBounded());
          else check("repeatable-read control retains first snapshot",equal && last.getSrcPriceCts()==1000 && last.isBounded());
          cases.add(Map.of("case",isolation==Connection.TRANSACTION_READ_COMMITTED?"READ COMMITTED cached-category counterexample":"REPEATABLE READ control only",
              "first_category",first,"reread_category",last,"calls",s.calls,"selected",s.selected,"cached_result_equal",equal,"barrier","writer commit after ticket selection and guard, before third category SELECT"));
        }finally{change(writer,"src_price_cts=1000,bounded=true");}
      }
    }
    try(var writer=connect()){
      try(var s=new Session(Connection.TRANSACTION_READ_COMMITTED)){
        s.beforeReread=()->change(writer,"tc_status='NOT_ACTIVE'");Throwable error=s.outer(3);
        check("reread inactive category raises actual missing-row exception",error instanceof org.springframework.dao.EmptyResultDataAccessException);
        check("earlier category existed and selected rows remain locked",s.reads.size()==2 && s.selected.equals(List.of(1,2,3)) && lockAttempt(1).equals("55P03"));
        cases.add(Map.of("case","category deactivation between reads","first_category",s.reads.get(1),"calls",s.calls,"error",error.getClass().getName(),"cached_reuse_would_skip_exception",true));
      }finally{change(writer,"tc_status='ACTIVE'");}
    }
    Map<String,Object> after=snapshot();save("after-full-state.json",after);check("all tables and sequences restored exactly",before.equals(after));
    save("result.json",Map.of("status","passed","checks",checks,"cases",cases,
        "origin",TicketReservationManager.class.getProtectionDomain().getCodeSource().getLocation().toString(),
        "scope","original helper and original outer method through post-reread sentinel; manual caller transactions, real NPJT and PostgreSQL; not full Spring/HTTP replay"));
  }
}
