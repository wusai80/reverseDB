package research.alfio;

import alfio.util.Json;
import java.io.*;
import java.nio.file.*;
import java.util.*;

/** Persistent JVM; each replay starts/closes its own original Spring context outside timing. */
public final class AlfioPerformanceWorker {
  static void clearTestCollection(Class<?> owner,String field) throws Exception {
    var f=owner.getDeclaredField(field);f.setAccessible(true);Object value=f.get(null);
    if(value instanceof Collection<?> c)c.clear();
    else if(value instanceof Map<?,?> m)m.clear();
    else throw new AssertionError(field);
  }
  public static void main(String[] args) throws Exception {
    Files.writeString(Path.of(args[0]),Json.toJson(Map.of("pid",ProcessHandle.current().pid(),
      "repository",alfio.repository.TicketRepository.class.getProtectionDomain().getCodeSource().getLocation().toString(),
      "helper",AlfioOrderedBatch.class.getProtectionDomain().getCodeSource().getLocation().toString())));
    try(BufferedReader input=new BufferedReader(new InputStreamReader(System.in))) {
      String line;
      while((line=input.readLine())!=null) {
        if(line.equals("stop"))return;
        Map request=Json.fromJson(line,Map.class);
        Path output=Path.of((String)request.get("output"));
        try {
          // Reset only test observations, never application data or business implementation.
          ReplayEntropy.finish();clearTestCollection(ReplayEntropy.class,"events");clearTestCollection(ReplayEntropy.class,"counts");
          clearTestCollection(AlfioOrderedBatch.class,"EVENTS");AlfioTiming.EVENTS.clear();AlfioPerformanceProbe.checks.clear();
          AlfioPerformanceProbe.main(new String[]{(String)request.get("url"),output.toString(),"replay"});
          Files.writeString(output.resolveSibling(output.getFileName()+"-timing.json"),Json.toJson(AlfioTiming.EVENTS));
          Path done=output.resolveSibling(output.getFileName()+"-done.json");
          Path tmp=output.resolveSibling(output.getFileName()+"-done.tmp");
          Files.writeString(tmp,Json.toJson(Map.of("status","passed","operations",AlfioTiming.EVENTS.size())));
          Files.move(tmp,done,StandardCopyOption.ATOMIC_MOVE);
        } catch(Throwable error) {
          error.printStackTrace();
          Files.writeString(output.resolveSibling(output.getFileName()+"-done.json"),Json.toJson(Map.of("status","failed","error",error.toString())));
          System.exit(1);
        }
      }
    }
  }
}
