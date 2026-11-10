package research;

import java.io.*;
import java.lang.reflect.*;
import java.net.*;
import java.nio.file.*;
import java.util.*;
import org.json.*;

/** One JVM, four persistent isolated adapters, complete application entry each run. */
public final class FusionAblationProbe {
  public static void main(String[] args) throws Exception {
    Map<String,URLClassLoader> loaders=new LinkedHashMap<>();
    Map<String,Method> entries=new LinkedHashMap<>();
    try {
      for (int i=0;i<4;i++) {
        String arm=new String[]{"original","neworder_only","payment_only","combined"}[i];
        URLClassLoader loader=new URLClassLoader(new URL[]{Path.of(args[i]).toUri().toURL(),Path.of(args[4]).toUri().toURL()},FusionAblationProbe.class.getClassLoader());
        loaders.put(arm,loader);
        Class<?> type=loader.loadClass("com.oltpbenchmark.api.TpccApp");
        if(!Path.of(type.getProtectionDomain().getCodeSource().getLocation().toURI()).equals(Path.of(args[i])))
          throw new AssertionError("Wrong adapter source: "+arm);
        entries.put(arm,type.getMethod("main",String[].class));
      }
      try (BufferedReader input=new BufferedReader(new InputStreamReader(System.in))) {
        for(String line;(line=input.readLine())!=null;) {
          JSONObject run=new JSONObject(line);
          try {
            entries.get(run.getString("arm")).invoke(null,(Object)new String[]{run.getString("url"),"tpcc",run.getString("commands"),run.getString("output"),"reference"});
          } catch(InvocationTargetException error) {
            if(error.getCause() instanceof Exception cause) throw cause;
            throw error;
          }
          Files.writeString(Path.of(run.getString("output"),"complete"),"passed\n");
        }
      }
    } finally { for(URLClassLoader loader:loaders.values()) loader.close(); }
  }
}
