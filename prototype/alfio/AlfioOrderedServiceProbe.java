package research.alfio;

import alfio.util.Json;
import com.google.gson.*;
import org.springframework.dao.EmptyResultDataAccessException;
import java.nio.file.*;
import java.sql.*;
import java.util.*;
import java.util.concurrent.*;

/** Mechanism test, not an installed model-decoding adapter or full equivalence proof. */
public final class AlfioOrderedServiceProbe {
    static List<String> checks=new ArrayList<>();static List<Object> cases=new ArrayList<>();
    static final long LOCK=73006011L;
    static void check(String name,boolean ok){if(!ok)throw new AssertionError(name);checks.add(name);}
    static JsonObject execute(AlfioServiceJoinAudit.Session s,String function,Integer... keys) throws Exception {
        if(!Set.of("ordered_service_lookup","ordered_service_lookup_barrier","ordered_service_lookup_stable").contains(function))throw new IllegalArgumentException();
        var ids=s.connection.createArrayOf("integer",keys);
        try {return JsonParser.parseString(s.jdbc.queryForObject("select reversedb_probe."+function+"(?,?)::text",String.class,1,ids)).getAsJsonObject();}
        finally {ids.free();}
    }
    static List<Integer> prices(JsonObject envelope) {
        List<Integer> result=new ArrayList<>();
        for(var record:envelope.getAsJsonArray("records"))result.add(record.getAsJsonObject().getAsJsonObject("definition").get("src_price_cts").getAsInt());
        return result;
    }
    static RuntimeException translate(JsonObject envelope) {
        if(envelope.get("status").getAsString().equals("cardinality_error") && envelope.get("actual").getAsInt()==0)
            return new EmptyResultDataAccessException(1);
        throw new IllegalArgumentException("Unsupported test envelope");
    }
    static String sqlState(Throwable error) {
        for(Throwable p=error;p!=null;p=p.getCause())if(p instanceof SQLException s)return s.getSQLState();
        return "none";
    }
    static void awaitBarrier(Connection control,int pid,Future<?> future) throws Exception {
        long deadline=System.nanoTime()+TimeUnit.SECONDS.toNanos(15);
        while(System.nanoTime()<deadline) {
            if(future.isDone()){future.get();throw new AssertionError("query finished before barrier");}
            try(var statement=control.prepareStatement("select exists(select 1 from pg_locks where pid=? and locktype='advisory' and objid::bigint=? and not granted)")) {
                statement.setInt(1,pid);statement.setLong(2,LOCK);
                try(var rs=statement.executeQuery()){rs.next();if(rs.getBoolean(1))return;}
            }
            Thread.sleep(10); // only polls observed lock state; never schedules the update by elapsed time
        }
        throw new AssertionError("barrier timeout");
    }
    public static void main(String[] args) throws Exception {
        AlfioServiceJoinAudit.url=args[0];Path out=Path.of(args[1]);
        try(var s=new AlfioServiceJoinAudit.Session("alfio_user",null);var writer=AlfioServiceJoinAudit.connect("alfio_user")) {
            writer.setAutoCommit(false);
            var original=s.calculator.collectAdditionalServiceItems(AlfioServiceJoinAudit.rid,s.event);
            Integer[] keys=original.stream().map(p->p.getLeft().id()).toArray(Integer[]::new);
            check("use original collector group order",Arrays.equals(keys,new Integer[]{101,102}));
            var full=execute(s,"ordered_service_lookup",keys);
            check("stable ordered records and attempts",full.get("status").getAsString().equals("ok") && full.get("attempts").getAsInt()==keys.length);
            for(int i=0;i<keys.length;i++) {
                var expected=JsonParser.parseString(s.jdbc.queryForObject("select to_jsonb(s)::text from additional_service_with_currency s where id=? and event_id_fk=1",String.class,keys[i]));
                var actual=full.getAsJsonArray("records").get(i).getAsJsonObject();
                check("all definition columns and computed view fields "+i,actual.get("key").getAsInt()==keys[i] && actual.get("definition").equals(expected));
            }
            cases.add(Map.of("case","stable full definitions in supplied order","envelope",full));s.connection.commit();
            for(int isolation:new int[]{Connection.TRANSACTION_READ_COMMITTED,Connection.TRANSACTION_REPEATABLE_READ}) {
                s.connection.setTransactionIsolation(isolation);
                for(String variant:List.of("ordered_service_lookup_barrier","ordered_service_lookup_stable")) {
                    AlfioServiceJoinAudit.prices(writer,100);s.clear();
                    s.beforeSecond=()->AlfioServiceJoinAudit.prices(writer,200);
                    var old=s.calculator.collectAdditionalServiceItems(AlfioServiceJoinAudit.rid,s.event).stream().map(p->p.getLeft().srcPriceCts()).toList();
                    s.connection.rollback();AlfioServiceJoinAudit.prices(writer,100);
                    int pid=s.jdbc.queryForObject("select pg_backend_pid()",Integer.class);
                    try(var statement=writer.createStatement()){statement.execute("select pg_advisory_lock("+LOCK+")");}
                    writer.commit();ExecutorService executor=Executors.newSingleThreadExecutor();
                    Future<JsonObject> future=executor.submit(()->execute(s,variant,keys));
                    try {
                        awaitBarrier(writer,pid,future);
                        AlfioServiceJoinAudit.prices(writer,200);
                        try(var statement=writer.createStatement()){statement.execute("select pg_advisory_unlock("+LOCK+")");}
                        writer.commit();var candidate=future.get(15,TimeUnit.SECONDS);var actual=prices(candidate);
                        List<Integer> expected=(isolation==Connection.TRANSACTION_READ_COMMITTED && variant.endsWith("_barrier"))?List.of(100,200):List.of(100,100);
                        check("controlled snapshot "+isolation+" "+variant,actual.equals(expected));
                        if(variant.endsWith("_barrier"))check("VOLATILE agrees with original "+isolation,actual.equals(old));
                        else if(isolation==Connection.TRANSACTION_READ_COMMITTED)check("STABLE negative control differs from original",!actual.equals(old));
                        cases.add(Map.of("case","concurrent ordered lookups","isolation",isolation,"function",variant,
                            "original",old,"candidate",actual,"barrier_observed",true));
                    }finally {
                        try(var statement=writer.createStatement()){statement.execute("select pg_advisory_unlock_all()");}
                        writer.commit();executor.shutdown();if(!executor.awaitTermination(20,TimeUnit.SECONDS))executor.shutdownNow();
                        s.connection.rollback();
                    }
                }
            }
            s.connection.setTransactionIsolation(Connection.TRANSACTION_READ_COMMITTED);AlfioServiceJoinAudit.prices(writer,100);s.clear();
            RuntimeException missing=null;
            try{s.services.getById(103,1);}catch(RuntimeException e){missing=e;}
            check("original missing definition is client cardinality error",missing instanceof EmptyResultDataAccessException);
            check("original missing leaves SQL transaction usable",s.jdbc.queryForObject("select 1",Integer.class)==1);
            var stopped=execute(s,"ordered_service_lookup",101,103,102);var translated=translate(stopped);
            check("server missing stops before later definition",stopped.get("attempts").getAsInt()==2 && stopped.getAsJsonArray("records").size()==1);
            check("translated missing class and message match",translated.getClass()==missing.getClass() && translated.getMessage().equals(missing.getMessage()));
            check("server marker leaves SQL transaction usable",s.jdbc.queryForObject("select 1",Integer.class)==1);
            cases.add(Map.of("case","missing cardinality marker","envelope",stopped,"translated_exception",translated.getClass().getName()));s.connection.rollback();
            try(var restricted=new AlfioServiceJoinAudit.Session("probe_items_only",s.event)) {
                var empty=execute(restricted,"ordered_service_lookup");
                check("invoker empty skips service privilege check",empty.get("attempts").getAsInt()==0 && empty.getAsJsonArray("records").isEmpty());
                String originalState="none",candidateState="none";
                try{restricted.services.getById(101,1);}catch(RuntimeException e){originalState=sqlState(e);}restricted.connection.rollback();
                try{execute(restricted,"ordered_service_lookup",101);}catch(RuntimeException e){candidateState=sqlState(e);}restricted.connection.rollback();
                check("nonempty invoker preserves permission rejection",originalState.equals("42501") && candidateState.equals(originalState));
                cases.add(Map.of("case","invoker permissions","empty_ok",true,"original_nonempty_sqlstate",originalState,"candidate_nonempty_sqlstate",candidateState));
            }
            s.jdbc.execute("set alfio.checkRowAccess='true'");s.jdbc.execute("set alfio.currentUserOrgs='999'");
            var hidden=execute(s,"ordered_service_lookup",101);
            check("invoker RLS hides definition",hidden.get("status").getAsString().equals("cardinality_error") && hidden.get("actual").getAsInt()==0);
            cases.add(Map.of("case","invoker RLS","envelope",hidden));s.connection.rollback();
            // Schema permits an unknown enum string. Original Java mapping fails
            // before the second query; JSON materialization does not enforce it.
            s.jdbc.update("update additional_service set vat_type='NOT_A_VAT_TYPE' where id=101");s.connection.commit();s.clear();
            String mappingFailure="none";
            try{s.calculator.collectAdditionalServiceItems(AlfioServiceJoinAudit.rid,s.event);}catch(RuntimeException e){mappingFailure=e.getClass().getName();}
            var raw=execute(s,"ordered_service_lookup",keys);
            check("original mapper fails before second definition",!mappingFailure.equals("none") && s.lookups.equals(List.of(101)));
            check("raw JSON helper exposes unresolved mapping timing",raw.get("status").getAsString().equals("ok") && raw.get("attempts").getAsInt()==2);
            cases.add(Map.of("case","materialization timing counterexample","original_exception",mappingFailure,
                "original_lookup_keys",new ArrayList<>(s.lookups),"raw_server_attempts",2,"candidate_admitted",false));
            s.jdbc.update("update additional_service set vat_type='INHERITED' where id=101");s.connection.commit();
        }
        Files.writeString(out.resolve("result.json"),Json.GSON.toJson(Map.of("status","passed","checks",checks,"cases",cases,
            "decision","sequential VOLATILE lookup mechanism supported in tested cases; model materialization timing unresolved; no backend admission",
            "client_call_model","retain original item read/grouping; G=0 skips helper (1 call), G>0 uses one helper (2 calls); no timing claim")));
    }
}
