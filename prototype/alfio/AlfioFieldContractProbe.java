package research.alfio;

import alfio.model.*;
import alfio.model.PurchaseContextFieldConfiguration.Context;
import alfio.model.modification.AttendeeData;
import alfio.repository.PurchaseContextFieldRepository;
import alfio.util.Json;
import ch.digitalfondue.npjt.ConstructorAnnotationRowMapper.Column;
import java.lang.reflect.*;
import java.nio.file.*;
import java.time.ZonedDateTime;
import java.util.*;

/** Executes original binary default methods. Abstract query calls are recorded,
 * not sent to a DB; this characterizes control/value flow, not SQL equivalence. */
public class AlfioFieldContractProbe {
    static final List<Object> cases = new ArrayList<>();
    static final List<String> checks = new ArrayList<>();
    static void check(String name, boolean ok) {
        if (!ok) throw new AssertionError(name);
        checks.add(name);
    }
    static Event event(String version) throws Exception {
        var constructor = Event.class.getConstructors()[0];
        var params = constructor.getParameters();
        Object[] args = new Object[params.length];
        for (int i=0; i<params.length; i++) {
            String name = params[i].getAnnotation(Column.class).value();
            args[i] = switch(name) {
                case "id" -> 7;
                case "org_id" -> 11;
                case "version" -> version;
                case "time_zone" -> "UTC";
                case "start_ts", "end_ts" -> ZonedDateTime.parse("2026-11-01T00:00:00Z");
                default -> params[i].getType() == int.class ? 0 : null;
            };
        }
        return (Event)constructor.newInstance(args);
    }
    static PurchaseContextFieldConfiguration config(int id, String name, boolean editable) {
        return new PurchaseContextFieldConfiguration(id,7,null,name,0,"text",null,null,
            false,editable,null,Context.ATTENDEE,null,null,null,false);
    }
    static PurchaseContextFieldValue value(long id, String name) {
        // Deliberately distinct from invocation ticket ID: update/delete must
        // use the IDs of the value read, whereas insert uses invocation IDs.
        return new PurchaseContextFieldValue(91,null,null,id,name,"old",Context.ATTENDEE);
    }
    static class Capture {
        List<PurchaseContextFieldConfiguration> configs = List.of();
        List<PurchaseContextFieldValue> values = List.of();
        List<String> names = new ArrayList<>();
        List<List<Object>> args = new ArrayList<>();
        String fail;
        RuntimeException injected = new IllegalStateException("synthetic query failure");
        int affected = 0; // calls must not be confused with affected rows
        PurchaseContextFieldRepository repo = (PurchaseContextFieldRepository)Proxy.newProxyInstance(
            PurchaseContextFieldRepository.class.getClassLoader(),new Class<?>[]{PurchaseContextFieldRepository.class},
            (proxy,method,arguments)->{
                if(method.isDefault()) return InvocationHandler.invokeDefault(proxy,method,arguments);
                String name=method.getName();
                names.add(name); args.add(Arrays.asList(arguments.clone()));
                if(name.equals(fail)) throw injected;
                return switch(name) {
                    case "findAdditionalFieldsForEvent" -> configs;
                    case "findAllByTicketId", "findAllForContextByTicketId" -> values;
                    case "updateValue", "deleteValue", "insertValue" -> affected;
                    default -> throw new AssertionError("unexpected abstract call "+name);
                };
            });
    }
    static Map<String,List<String>> input(String key,List<String> value) {
        Map<String,List<String>> result=new LinkedHashMap<>(); result.put(key,value); return result;
    }
    static void run(String label,Capture c,Event event,Map<String,List<String>> input,
                    Class<? extends Throwable> error,String... expected) {
        Throwable failure=null;
        try { c.repo.updateOrInsert(input,event,42,null); }
        catch(Throwable t) { failure=t; }
        check(label+" calls",c.names.equals(Arrays.asList(expected)));
        check(label+" outcome",error==null ? failure==null : error.isInstance(failure));
        if(c.fail!=null) check(label+" exception identity",failure==c.injected);
        Map<String,Object> record=new LinkedHashMap<>();
        record.put("case",label);record.put("calls",c.names);record.put("arguments",c.args);
        record.put("failure",failure==null?null:failure.getClass().getName());cases.add(record);
    }
    public static void main(String[] argv) throws Exception {
        Event old=event(null), linked=event("9999.0");
        String read="findAdditionalFieldsForEvent", values="findAllByTicketId";
        check("actual Event version predicate",!old.supportsLinkedAdditionalServices() && linked.supportsLinkedAdditionalServices());
        check("actual attendee null map skips parent call",!new AttendeeData(null,null,null,null,null,null,null).hasAdditionalData());
        check("actual attendee empty map enters parent call",new AttendeeData(null,null,null,null,null,null,Map.of()).hasAdditionalData());
        run("null map still reads",new Capture(),old,null,null,read,values);
        run("empty map still reads",new Capture(),old,Map.of(),null,read,values);
        Capture linkedRead=new Capture();
        run("linked read is exclusive",linkedRead,linked,Map.of(),null,read,"findAllForContextByTicketId");
        check("linked read retains ticket and context",linkedRead.args.get(1).equals(Arrays.asList(42,Context.ATTENDEE)));
        Capture mixed=new Capture();
        mixed.configs=List.of(config(1,"update",true),config(2,"delete",true),config(3,"insert",true),
            config(4,"existingReadOnly",false),config(5,"missingReadOnly",false),config(6,"blankMissing",true));
        mixed.values=List.of(value(21,"update"),value(22,"delete"),value(24,"existingReadOnly"));
        Map<String,List<String>> fields=new LinkedHashMap<>();
        fields.put("update",List.of("new")); fields.put("delete",List.of(" "));
        fields.put("insert",Arrays.asList("a",null,"a",""));
        fields.put("existingReadOnly",List.of("new")); fields.put("missingReadOnly",List.of("new"));
        fields.put("unknown",List.of("new"));fields.put("blankMissing",List.of(""));
        run("all branches in input order",mixed,old,fields,null,read,values,"updateValue","deleteValue","insertValue","insertValue");
        check("update uses stored IDs and exact scalar",mixed.args.get(2).equals(Arrays.asList(91,null,21L,"new")));
        check("reads retain event and ticket IDs",mixed.args.get(0).equals(List.of(7)) && mixed.args.get(1).equals(List.of(42)));
        check("delete uses stored IDs",mixed.args.get(3).equals(Arrays.asList(91,null,22L)));
        check("insert complete list and invocation IDs",mixed.args.get(4).equals(Arrays.asList(42,null,11,3L,"[\"a\",null,\"a\",\"\"]",Context.ATTENDEE)));
        check("missing readonly can insert",mixed.args.get(5).get(3).equals(5L));
        check("normal cost 2 plus writes even with zero affected rows",mixed.names.size()==2+4);
        var converter=new Capture().repo;
        check("single null preserved",converter.getFieldValueJson(Arrays.asList((String)null))==null);
        check("single scalar preserved",converter.getFieldValueJson(List.of(" a ")).equals(" a "));
        check("empty values blank",converter.getFieldValueJson(List.of()).equals(""));
        check("all blank values blank",converter.getFieldValueJson(Arrays.asList(null," ","\t")).equals(""));
        Capture unconfiguredExisting=new Capture();unconfiguredExisting.values=List.of(value(30,"stored"));
        run("existing unconfigured value can update",unconfiguredExisting,old,input("stored",List.of("x")),null,read,values,"updateValue");
        Capture singleNull=new Capture();singleNull.values=List.of(value(30,"stored"));
        run("single null deletes existing value",singleNull,old,input("stored",Arrays.asList((String)null)),null,read,values,"deleteValue");
        Capture nullList=new Capture();nullList.configs=List.of(config(1,"first",true));
        var invalid=input("first",List.of("x"));invalid.put("unknown",null);invalid.put("later",List.of("x"));
        run("null unknown field fails after prior write",nullList,old,invalid,NullPointerException.class,read,values,"insertValue");
        Capture duplicateConfig=new Capture();duplicateConfig.configs=List.of(config(1,"x",true),config(2,"x",true));
        run("duplicate config before writes",duplicateConfig,old,input("x",List.of("x")),IllegalStateException.class,read,values);
        Capture duplicateValue=new Capture();duplicateValue.values=List.of(value(1,"x"),value(2,"x"));
        run("duplicate stored name before writes",duplicateValue,old,input("x",List.of("x")),IllegalStateException.class,read,values);
        for(String failed:List.of(read,values,"insertValue","updateValue","deleteValue")) {
            Capture c=new Capture();c.fail=failed;c.configs=List.of(config(1,"x",true),config(2,"y",true));
            var in=input("x",List.of("x"));in.put("y",List.of("y"));
            if(failed.equals("updateValue") || failed.equals("deleteValue")) c.values=List.of(value(1,"x"));
            if(failed.equals("deleteValue")) in.put("x",List.of(""));
            String[] expected=failed.equals(read)?new String[]{read}:failed.equals(values)?new String[]{read,values}:new String[]{read,values,failed};
            run("failure stops at "+failed,c,old,in,IllegalStateException.class,expected);
        }
        Path out=Path.of(argv[0]);Files.createDirectories(out);
        Files.writeString(out.resolve("binary-probe.json"),Json.toJson(Map.of("status","passed","checks",checks,"cases",cases,
            "origin",PurchaseContextFieldRepository.class.getProtectionDomain().getCodeSource().getLocation().toString())));
    }
}
