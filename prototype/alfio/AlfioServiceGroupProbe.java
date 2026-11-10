package research.alfio;

import alfio.manager.support.reservation.ReservationCostCalculator;
import alfio.model.*;
import alfio.repository.*;
import alfio.util.Json;
import org.apache.commons.lang3.tuple.Pair;
import java.lang.reflect.*;
import java.nio.file.*;
import java.util.*;
import java.util.stream.*;

/** Runs original collector methods; repositories are recording test proxies. */
public final class AlfioServiceGroupProbe {
    static List<String> checks=new ArrayList<>();
    static List<Object> cases=new ArrayList<>();
    static Event event;
    static void check(String name,boolean condition) {
        if(!condition)throw new AssertionError(name);checks.add(name);
    }
    static AdditionalServiceItem item(int id,int service) {
        return new AdditionalServiceItem(id,"u"+id,null,null,"reservation",service,
            AdditionalServiceItem.AdditionalServiceItemStatus.PENDING,7,100,100,0,0,"EUR",null);
    }
    static AdditionalService definition(int id) {
        return new AdditionalService(id,7,false,0,100,100,null,null,null,
            AdditionalService.VatType.INHERITED,100,AdditionalService.AdditionalServiceType.SUPPLEMENT,
            AdditionalService.SupplementPolicy.OPTIONAL_UNLIMITED_AMOUNT,"EUR",100,null,null);
    }
    static class Capture {
        List<AdditionalServiceItem> items;
        List<String> names=new ArrayList<>();List<List<Object>> arguments=new ArrayList<>();
        Map<Integer,AdditionalService> definitions=new HashMap<>();
        int failLookup=0;boolean failRead=false,returnNull=false;
        RuntimeException sentinel=new IllegalStateException("synthetic repository failure");
        ReservationCostCalculator calculator;
        Capture(List<AdditionalServiceItem> items) {
            this.items=items;
            AdditionalServiceItemRepository itemRepository=(AdditionalServiceItemRepository)Proxy.newProxyInstance(
                AdditionalServiceItemRepository.class.getClassLoader(),new Class<?>[]{AdditionalServiceItemRepository.class},
                (proxy,method,args)->{
                    if(!method.getName().equals("findByReservationUuid"))throw new AssertionError(method);
                    names.add(method.getName());arguments.add(Arrays.asList(args.clone()));
                    if(failRead)throw sentinel;
                    return this.items;
                });
            AdditionalServiceRepository serviceRepository=(AdditionalServiceRepository)Proxy.newProxyInstance(
                AdditionalServiceRepository.class.getClassLoader(),new Class<?>[]{AdditionalServiceRepository.class},
                (proxy,method,args)->{
                    if(!method.getName().equals("getById"))throw new AssertionError(method);
                    names.add(method.getName());arguments.add(Arrays.asList(args.clone()));
                    if(names.size()-1==failLookup)throw sentinel;
                    return returnNull?null:definitions.computeIfAbsent((Integer)args[0],AlfioServiceGroupProbe::definition);
                });
            calculator=new ReservationCostCalculator(null,null,null,null,null,serviceRepository,itemRepository);
        }
        @SuppressWarnings("unchecked")
        Stream<Pair<AdditionalService,List<AdditionalServiceItem>>> stream() throws Throwable {
            Method method=ReservationCostCalculator.class.getDeclaredMethod("streamAdditionalServiceItems",String.class,PurchaseContext.class);
            method.setAccessible(true);
            try{return (Stream<Pair<AdditionalService,List<AdditionalServiceItem>>>)method.invoke(calculator,"reservation",event);}
            catch(InvocationTargetException error){throw error.getCause();}
        }
    }
    static void record(String label,Capture c,Object detail) {
        cases.add(Map.of("case",label,"calls",c.names,"arguments",c.arguments,"detail",detail));
    }
    static void complete(String label,List<AdditionalServiceItem> input) {
        Capture c=new Capture(input);
        var before=new ArrayList<>(input);
        var result=c.calculator.collectAdditionalServiceItems("reservation",event);
        Map<Integer,List<AdditionalServiceItem>> expected=new HashMap<>();
        for(var value:input)expected.computeIfAbsent(value.getAdditionalServiceId(),ignored->new ArrayList<>()).add(value);
        check(label+" one read plus distinct groups",c.names.size()==1+expected.size() && result.size()==expected.size());
        check(label+" complete read arguments",c.arguments.get(0).equals(List.of(7,"reservation")));
        Set<Integer> seen=new HashSet<>();int itemCount=0;
        for(int i=0;i<result.size();i++) {
            var pair=result.get(i);int key=pair.getLeft().id();var members=expected.get(key);
            check(label+" unique lookup group "+i,seen.add(key) && c.arguments.get(i+1).equals(List.of(key,7)));
            check(label+" definition identity "+i,pair.getLeft()==c.definitions.get(key));
            check(label+" member count "+i,pair.getRight().size()==members.size());
            for(int j=0;j<members.size();j++)check(label+" member identity and order "+i+":"+j,pair.getRight().get(j)==members.get(j));
            itemCount+=pair.getRight().size();
        }
        check(label+" no lost or deduplicated items",itemCount==input.size());
        check(label+" source list unmodified",before.equals(input));
        record(label,c,Map.of("items",itemCount,"groups",result.size()));
    }
    public static void main(String[] args) throws Throwable {
        event=AlfioFieldContractProbe.event(null);
        complete("empty",List.of());
        complete("one group",List.of(item(1,3),item(2,3)));
        var repeated=item(10,17);var equalFields=item(10,17);
        complete("interleaved duplicate identity and equal fields",List.of(repeated,item(20,-1),equalFields,repeated,item(30,16),item(21,-1)));
        complete("integer key boundaries",List.of(item(1,Integer.MIN_VALUE),item(2,Integer.MAX_VALUE),item(3,0)));
        Capture lazy=new Capture(List.of(item(1,7),item(2,23),item(3,7)));
        try(var stream=lazy.stream()) {
            check("stream creation reads all items but no definitions",lazy.names.equals(List.of("findByReservationUuid")));
            var first=stream.limit(1).toList();
            check("partial consumption looks up one group only",first.size()==1 && lazy.names.size()==2);
        }
        record("lazy partial consumption",lazy,"not the eager collector cost domain");
        Capture never=new Capture(List.of(item(1,9)));
        try(var stream=never.stream()) {check("unconsumed stream still reads and groups",never.names.size()==1);}
        check("closing does not trigger missing lookups",never.names.size()==1);record("unconsumed stream",never,"no terminal collection");
        Capture counted=new Capture(List.of(item(1,1),item(2,2)));
        try(var stream=counted.stream()) {
            check("count can skip sized stream map effects in pinned JDK",stream.count()==2 && counted.names.size()==1);
        }
        record("count terminal",counted,"sized-stream count skips definition map; eager collector contract must not be generalized to count");
        for(int index:new int[]{1,2}) {
            Capture c=new Capture(List.of(item(1,1),item(2,2),item(3,3)));c.failLookup=index;
            try {c.calculator.collectAdditionalServiceItems("reservation",event);throw new AssertionError("failure missing");}
            catch(RuntimeException error){check("lookup failure identity "+index,error==c.sentinel);}
            check("lookup failure stops prefix "+index,c.names.size()==1+index);
            record("failure on lookup "+index,c,"no later group lookup");
        }
        Capture readFailure=new Capture(List.of());readFailure.failRead=true;
        try {readFailure.calculator.collectAdditionalServiceItems("reservation",event);throw new AssertionError("failure missing");}
        catch(RuntimeException error){check("read failure identity",error==readFailure.sentinel);}
        check("read failure has no group effects",readFailure.names.size()==1);record("read failure",readFailure,"stopped");
        Capture nullItem=new Capture(Arrays.asList(item(1,1),null,item(2,2)));
        try {nullItem.calculator.collectAdditionalServiceItems("reservation",event);throw new AssertionError("failure missing");}
        catch(NullPointerException error){check("null item fails during eager grouping",nullItem.names.size()==1);}
        record("null item before group lookups",nullItem,"stopped before any definition lookup");
        Capture nullDefinition=new Capture(List.of(item(1,4)));nullDefinition.returnNull=true;
        var result=nullDefinition.calculator.collectAdditionalServiceItems("reservation",event);
        check("null definition is retained by original collector",result.size()==1 && result.get(0).getLeft()==null && result.get(0).getRight().get(0)==nullDefinition.items.get(0));
        record("null definition",nullDefinition,"pair retained; later price handling outside this scope");
        Files.writeString(Path.of(args[0]).resolve("binary-probe.json"),Json.toJson(Map.of("status","passed","checks",checks,"cases",cases,
            "origin",ReservationCostCalculator.class.getProtectionDomain().getCodeSource().getLocation().toString())));
    }
}
