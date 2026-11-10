package research.alfio;

import java.util.*;

/** Timer wraps the service call, including synchronous transaction completion. */
public final class AlfioTiming {
  static final List<Map<String,Object>> EVENTS=new ArrayList<>();
  interface Call<T> { T call() throws Exception; }
  interface Action { void run() throws Exception; }
  static <T> T call(String operation,Call<T> action) throws Exception {
    long start=System.nanoTime();boolean returned=false;
    try { T value=action.call();returned=true;return value; }
    finally { long elapsed=System.nanoTime()-start;EVENTS.add(Map.of("operation",operation,"nanoseconds",elapsed,"returned",returned)); }
  }
  static void run(String operation,Action action) throws Exception { call(operation,()->{action.run();return null;}); }
}
