package reversedb.tpcc;

import java.lang.instrument.Instrumentation;

/** No bytecode rewriting: expose JVM-reported shallow object sizes only. */
public final class MemoryAgent {
  private static Instrumentation instrumentation;
  public static void premain(String args, Instrumentation value) { instrumentation=value; }
  public static long size(Object value) {
    if(instrumentation==null) throw new IllegalStateException("Memory sizing agent required");
    return instrumentation.getObjectSize(value);
  }
}
