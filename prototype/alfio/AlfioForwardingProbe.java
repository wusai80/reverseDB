package research.alfio;

import alfio.model.Audit;
import alfio.repository.AuditingRepository;
import alfio.util.Json;
import java.lang.reflect.*;
import java.nio.file.*;
import java.util.*;

public final class AlfioForwardingProbe {
    static List<String> checks=new ArrayList<>();
    static List<Object> cases=new ArrayList<>();
    static void check(String name,boolean condition) {
        if(!condition)throw new AssertionError(name);checks.add(name);
    }
    public static void main(String[] args) throws Exception {
        for(int count:new int[]{0,1,-3}) {
            List<Object[]> calls=new ArrayList<>();
            AuditingRepository repo=(AuditingRepository)Proxy.newProxyInstance(AuditingRepository.class.getClassLoader(),
                new Class<?>[]{AuditingRepository.class},(proxy,method,arguments)->{
                    if(method.isDefault())return InvocationHandler.invokeDefault(proxy,method,arguments);
                    check("SQL leaf descriptor "+count,method.getName().equals("insert") && method.getParameterCount()==8 && method.getParameterTypes()[7]==String.class);
                    calls.add(arguments.clone());return count;
                });
            String reservation=new String("same-content"), entity=new String("same-content");Date date=new Date(123456789L);
            int result=repo.insert(reservation,null,7,Audit.EventType.RESERVATION_CREATE,date,Audit.EntityType.RESERVATION,entity);
            check("one leaf call "+count,calls.size()==1);Object[] forwarded=calls.get(0);
            check("return passed unchanged "+count,result==count);
            check("equal strings retain distinct identity "+count,forwarded[0]==reservation && forwarded[6]==entity && forwarded[0]!=forwarded[6]);
            check("date reference unchanged "+count,forwarded[4]==date && date.getTime()==123456789L);
            check("all parameters and null extension "+count,Arrays.equals(forwarded,new Object[]{reservation,null,7,Audit.EventType.RESERVATION_CREATE,date,Audit.EntityType.RESERVATION,entity,null}));
            cases.add(Map.of("case","audit return "+count,"arguments",Arrays.asList(forwarded),"return",result));
        }
        RuntimeException sentinel=new IllegalStateException("synthetic SQL failure");int[] failedCalls={0};
        AuditingRepository failed=(AuditingRepository)Proxy.newProxyInstance(AuditingRepository.class.getClassLoader(),
            new Class<?>[]{AuditingRepository.class},(proxy,method,arguments)->{
                if(method.isDefault())return InvocationHandler.invokeDefault(proxy,method,arguments);
                failedCalls[0]++;throw sentinel;
            });
        try { failed.insert("r",null,7,Audit.EventType.RESERVATION_CREATE,new Date(0),Audit.EntityType.RESERVATION,"r");throw new AssertionError("no failure"); }
        catch(RuntimeException error) {check("audit exact failure identity",error==sentinel);}
        check("audit failure no retry",failedCalls[0]==1);
        cases.add(Map.of("case","audit failure","calls",failedCalls[0]));
        List<Object[]> calls=new ArrayList<>();
        ForwardingFixture fixture=(ForwardingFixture)Proxy.newProxyInstance(ForwardingFixture.class.getClassLoader(),
            new Class<?>[]{ForwardingFixture.class},(proxy,method,arguments)->{
                if(method.isDefault())return InvocationHandler.invokeDefault(proxy,method,arguments);
                calls.add(arguments.clone());return 19;
            });
        Object value=new Object();long wide=0x100000002L;
        check("transitive wrapper return",fixture.delegate(value,wide)==19);
        check("duplicate arguments preserve same instance and wide value",calls.size()==1 && calls.get(0)[0]==value && calls.get(0)[1]==value && calls.get(0)[2].equals(wide));
        calls.clear();check("discarded result keeps original return",fixture.discarded(value,wide)==7 && calls.size()==1);
        calls.clear();check("two terminal effects retained",fixture.twice(value,wide)==19 && calls.size()==2);
        cases.add(Map.of("case","synthetic transitive alias, wide value and discarded result controls","passed",true));
        Path out=Path.of(args[0]);
        Files.writeString(out.resolve("binary-probe.json"),Json.toJson(Map.of("status","passed","checks",checks,"cases",cases,
            "origin",AuditingRepository.class.getProtectionDomain().getCodeSource().getLocation().toString())));
    }
}
