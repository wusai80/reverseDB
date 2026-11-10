package research.alfio;

import alfio.manager.support.reservation.ReservationCostCalculator;
import alfio.model.*;
import alfio.repository.*;
import alfio.util.Json;
import ch.digitalfondue.npjt.QueryFactory;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.core.namedparam.NamedParameterJdbcTemplate;
import org.springframework.jdbc.datasource.SingleConnectionDataSource;
import java.lang.reflect.*;
import java.nio.file.*;
import java.sql.*;
import java.util.*;

/** Real original NPJT repositories, driver and calculator on an isolated restored
 * schema. JOIN is a diagnostic row shape, never installed as a backend. */
public final class AlfioServiceJoinAudit {
    static List<String> checks=new ArrayList<>();static List<Object> cases=new ArrayList<>();
    static String join, url;static Path out;
    static void check(String name,boolean ok){if(!ok)throw new AssertionError(name);checks.add(name);}
    static String rid="00000000-0000-0000-0000-000000000001";
    static String missing="00000000-0000-0000-0000-000000000002";
    static Connection connect(String user) throws SQLException {return DriverManager.getConnection(url,user,"synthetic-join-audit-only");}
    static class Session implements AutoCloseable {
        Connection connection;SingleConnectionDataSource ds;JdbcTemplate jdbc;NamedParameterJdbcTemplate named;
        AdditionalServiceItemRepository items;AdditionalServiceRepository services;Event event;
        List<String> calls=new ArrayList<>();List<Integer> lookups=new ArrayList<>();Runnable beforeSecond;
        ReservationCostCalculator calculator;
        Session(String user,Event supplied) throws Exception {
            connection=connect(user);connection.setAutoCommit(false);ds=new SingleConnectionDataSource(connection,true);
            jdbc=new JdbcTemplate(ds);named=new NamedParameterJdbcTemplate(ds);
            items=QueryFactory.from(AdditionalServiceItemRepository.class,"PGSQL",ds);
            services=QueryFactory.from(AdditionalServiceRepository.class,"PGSQL",ds);
            event=supplied==null?QueryFactory.from(EventRepository.class,"PGSQL",ds).findById(1):supplied;
            connection.commit();
            var itemProxy=(AdditionalServiceItemRepository)Proxy.newProxyInstance(items.getClass().getClassLoader(),new Class<?>[]{AdditionalServiceItemRepository.class},(proxy,m,args)->{
                calls.add(m.getName());return invoke(items,m,args);
            });
            var serviceProxy=(AdditionalServiceRepository)Proxy.newProxyInstance(services.getClass().getClassLoader(),new Class<?>[]{AdditionalServiceRepository.class},(proxy,m,args)->{
                calls.add(m.getName());lookups.add((Integer)args[0]);
                if(lookups.size()==2 && beforeSecond!=null)beforeSecond.run();
                return invoke(services,m,args);
            });
            calculator=new ReservationCostCalculator(null,null,null,null,null,serviceProxy,itemProxy);
        }
        static Object invoke(Object delegate,Method method,Object[] args) throws Throwable {
            try{return method.invoke(delegate,args);}catch(InvocationTargetException error){throw error.getCause();}
        }
        void clear(){calls.clear();lookups.clear();beforeSecond=null;}
        List<String> joinRows(String reservation,boolean inner) {
            return named.query(inner?join.replace("LEFT JOIN","INNER JOIN"):join,
                Map.of("eventId",1,"reservationId",reservation),
                (rs,n)->rs.getString("item_row")+"|"+rs.getString("definition_row"));
        }
        @Override public void close() throws Exception {connection.rollback();connection.close();}
    }
    static void save(String name,Object value) throws Exception {Files.writeString(out.resolve(name),Json.toJson(value));}
    static void prices(Connection writer,int price) {
        try(var statement=writer.createStatement()) {
            statement.executeUpdate("update additional_service set src_price_cts="+price+" where id in (101,102)");writer.commit();
        }catch(SQLException error){throw new RuntimeException(error);}
    }
    static List<Integer> joinPrices(Session s) {
        return s.named.query("SELECT DISTINCT (definition_row->>'src_price_cts')::int p FROM ("+join+") q ORDER BY p",
            Map.of("eventId",1,"reservationId",rid),(rs,n)->rs.getInt(1));
    }
    public static void main(String[] args) throws Exception {
        url=args[0];out=Path.of(args[1]);join=Files.readString(Path.of(args[2]));
        // These are new synthetic rows only in a throwaway full-schema restore.
        try(var s=new Session("alfio_user",null)) {
            save("runtime.json",s.jdbc.queryForMap("select current_user, current_setting('transaction_isolation') isolation, current_setting('default_transaction_isolation') default_isolation, current_setting('row_security') row_security, version() version"));
            check("application test role is not superuser",Boolean.FALSE.equals(s.jdbc.queryForObject("select usesuper from pg_user where usename=current_user",Boolean.class)));
            s.jdbc.update("insert into event select (json_populate_record(NULL::event, (to_jsonb(e)||'{\"id\":2,\"short_name\":\"join-audit-other\",\"private_key\":\"00000000-0000-0000-0000-000000000002\"}'::jsonb)::json)).* from event e where id=1");
            for(int id:new int[]{101,102,103})s.jdbc.update("insert into additional_service(id,event_id_fk,fix_price,available_qty,vat_type,src_price_cts,service_type,supplement_policy) values (?,?,false,10,'INHERITED',100,'SUPPLEMENT','OPTIONAL_UNLIMITED_AMOUNT')",id,id==103?2:1);
            for(int index=0;index<3;index++)s.jdbc.update("insert into additional_service_item(id,uuid,creation,tickets_reservation_uuid,additional_service_id_fk,status,event_id_fk,src_price_cts,final_price_cts,currency_code) values (?,?,TIMESTAMPTZ '2026-11-06 00:00:00+00',?,?,'PENDING',1,100,100,'CHF')",
                1001+index,String.format("00000000-0000-0000-0000-%012d",1001+index),rid,index==1?102:101);
            s.jdbc.update("insert into additional_service_item(id,uuid,creation,additional_service_id_fk,status,event_id_fk,currency_code) values (1004,'00000000-0000-0000-0000-000000001004',TIMESTAMPTZ '2026-11-06 00:00:00+00',101,'FREE',1,'CHF')");
            s.connection.commit();s.clear();
            var original=s.calculator.collectAdditionalServiceItems(rid,s.event);
            check("original real repositories one read plus two groups",s.calls.size()==3 && original.size()==2);
            check("all three original item rows preserved",original.stream().mapToInt(p->p.getRight().size()).sum()==3);
            check("view computed availability retained",original.stream().filter(p->p.getLeft().id()==101).findFirst().orElseThrow().getLeft().availableItems()==1);
            // Exact raw SQL rows (including fields unmapped by model constructors),
            // compared as a bag only. This deliberately proves no ordering claim.
            List<String> originalBag=new ArrayList<>();
            for(var p:original) {
                String definition=s.jdbc.queryForObject("select to_jsonb(s)::text from additional_service_with_currency s where id=? and event_id_fk=1",String.class,p.getLeft().id());
                for(var item:p.getRight())originalBag.add(s.jdbc.queryForObject("select to_jsonb(i)::text from additional_service_item i where id=?",String.class,item.getId())+"|"+definition);
            }
            var joined=s.joinRows(rid,false);var sortedOriginal=new ArrayList<>(originalBag);var sortedJoin=new ArrayList<>(joined);
            Collections.sort(sortedOriginal);Collections.sort(sortedJoin);
            check("stable full-row bag equality",sortedOriginal.equals(sortedJoin));
            save("stable-original-model.json",original);save("stable-original-full-rows.json",originalBag);save("stable-join-full-rows.json",joined);
            cases.add(Map.of("case","stable complete data","original_calls",new ArrayList<>(s.calls),"bag_equal",true,"order_proven",false));
            s.clear();check("empty original still performs one item query",s.calculator.collectAdditionalServiceItems(missing,s.event).isEmpty() && s.calls.size()==1);
            check("empty join empty",s.joinRows(missing,false).isEmpty());
            cases.add(Map.of("case","empty reservation","original_calls",new ArrayList<>(s.calls)));s.connection.commit();
            // All FK constraints stay enabled. The schema allows item.event != service.event.
            s.jdbc.update("insert into additional_service_item(id,uuid,creation,tickets_reservation_uuid,additional_service_id_fk,status,event_id_fk,currency_code) values (1005,'00000000-0000-0000-0000-000000001005',TIMESTAMPTZ '2026-11-06 00:00:00+00',?,103,'PENDING',1,'CHF')",missing);
            s.connection.commit();s.clear();
            String outcome;Object missingResult;
            try {missingResult=s.calculator.collectAdditionalServiceItems(missing,s.event);outcome="returned";}
            catch(RuntimeException error){outcome=error.getClass().getName();missingResult=error.getMessage();}
            s.connection.rollback();
            var left=s.joinRows(missing,false);var inner=s.joinRows(missing,true);
            check("legal FK cross-event row remains in left join",left.size()==1 && left.get(0).endsWith("|null"));
            check("inner join silently loses legal input item",inner.isEmpty());
            save("missing-definition.json",Map.of("original_outcome",outcome,"original_result",missingResult,"left_join",left,"inner_join",inner,"constraints_disabled",false));
            cases.add(Map.of("case","cross-event reference satisfies FK","original_outcome",outcome,"inner_join_drops_item",true));s.connection.commit();
            s.jdbc.execute("set alfio.checkRowAccess='true'");s.jdbc.execute("set alfio.currentUserOrgs='999'");
            check("RLS hidden original items empty",s.calculator.collectAdditionalServiceItems(rid,s.event).isEmpty());
            check("RLS hidden join items empty",s.joinRows(rid,false).isEmpty());
            cases.add(Map.of("case","RLS denies organization","both_empty",true));s.connection.rollback();
        }
        // Two real connections, explicit barriers via synchronous commits; no sleeps.
        try(var s=new Session("alfio_user",null);var writer=connect("alfio_user")) {
            writer.setAutoCommit(false);
            for(int isolation:new int[]{Connection.TRANSACTION_READ_COMMITTED,Connection.TRANSACTION_REPEATABLE_READ}) {
                s.connection.rollback();s.connection.setTransactionIsolation(isolation);prices(writer,100);s.clear();
                var before=joinPrices(s);
                s.beforeSecond=()->prices(writer,200);
                var original=s.calculator.collectAdditionalServiceItems(rid,s.event);
                var oldPrices=original.stream().map(p->p.getLeft().srcPriceCts()).toList();var after=joinPrices(s);
                if(isolation==Connection.TRANSACTION_READ_COMMITTED) {
                    check("read committed original sees mixed committed versions",oldPrices.equals(List.of(100,200)));
                    check("one join sees one committed version",before.equals(List.of(100)) && after.equals(List.of(200)));
                }else {
                    check("repeatable read control has stable versions",oldPrices.equals(List.of(100,100)) && before.equals(List.of(100)) && after.equals(List.of(100)));
                }
                cases.add(Map.of("case",isolation==2?"READ COMMITTED concurrent update":"REPEATABLE READ control", "lookup_order",new ArrayList<>(s.lookups),
                    "join_before",before,"original_group_prices",oldPrices,"join_after",after));s.connection.rollback();
            }
            prices(writer,100);
            try(var restricted=new Session("probe_items_only",s.event)) {
                check("restricted empty original never needs definition privilege",restricted.calculator.collectAdditionalServiceItems("empty",restricted.event).isEmpty());
                String state="none";
                try {restricted.joinRows("empty",false);}
                catch(RuntimeException error){Throwable cause=error;while(cause.getCause()!=null)cause=cause.getCause();state=cause instanceof SQLException sql?sql.getSQLState():cause.getClass().getName();}
                check("join checks definition privilege even when no items",state.equals("42501"));
                cases.add(Map.of("case","empty with item-only privileges","original_success",true,"join_sqlstate",state,
                    "scope","permission negative control, not a claim current application account has these grants"));
            }
        }
        save("result.json",Map.of("status","passed","checks",checks,"cases",cases,
            "origin",ReservationCostCalculator.class.getProtectionDomain().getCodeSource().getLocation().toString(),
            "decision","single JOIN not admitted under unchanged application contract"));
    }
}
