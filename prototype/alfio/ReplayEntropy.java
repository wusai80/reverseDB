package research.alfio;

import java.lang.instrument.*;
import java.security.*;
import java.nio.*;
import java.nio.charset.StandardCharsets;
import java.util.*;
import org.objectweb.asm.*;

/** Test-only source control shared by both arms. Never rewrites database/results. */
public final class ReplayEntropy {
  private static final long MILLIS=1793959200000L; // 2026-11-06T10:00:00Z
  private static String operation;
  private static Thread owner;
  private static final Map<String,Integer> counts=new HashMap<>();
  private static final List<Map<String,Object>> events=new ArrayList<>();
  private static final Map<String,Map<String,Object>> transformed=new TreeMap<>();
  public static synchronized void begin(String label) {
    operation=label; owner=Thread.currentThread(); counts.clear();
    events.add(Map.of("operation",label,"kind","begin"));
  }
  private static int next(String caller,String kind) {
    if(Thread.currentThread()!=owner) throw new IllegalStateException("Unexpected concurrent entropy call: "+caller);
    return counts.merge(caller+":"+kind,1,Integer::sum);
  }
  public static synchronized UUID uuid(String caller) {
    if(operation==null) return UUID.randomUUID();
    int index=next(caller,"uuid");
    try {
      byte[] b=MessageDigest.getInstance("SHA-256").digest(("20261106|"+operation+"|"+caller+"|"+index).getBytes(StandardCharsets.UTF_8));
      b[6]=(byte)((b[6]&15)|64); b[8]=(byte)((b[8]&63)|128);
      var buffer=ByteBuffer.wrap(b); var id=new UUID(buffer.getLong(),buffer.getLong());
      events.add(Map.of("operation",operation,"caller",caller,"kind","uuid","index",index,"value",id.toString()));
      return id;
    } catch(NoSuchAlgorithmException error) { throw new AssertionError(error); }
  }
  public static synchronized long millis(String caller) {
    if(operation==null) return System.currentTimeMillis();
    int index=next(caller,"millis");
    events.add(Map.of("operation",operation,"caller",caller,"kind","millis","index",index,"value",MILLIS));
    return MILLIS;
  }
  public static synchronized List<Map<String,Object>> finish() { operation=null; owner=null; return List.copyOf(events); }
  public static synchronized Map<String,Map<String,Object>> transformations() { return new TreeMap<>(transformed); }
  public static void premain(String args,Instrumentation instrumentation) {
    instrumentation.addTransformer(new ClassFileTransformer() {
      @Override public byte[] transform(ClassLoader loader,String name,Class<?> type,ProtectionDomain domain,byte[] bytes) {
        if(name==null || !name.startsWith("alfio/")) return null;
        try {
          int[] replacements={0,0,0};
          var reader=new ClassReader(bytes); var writer=new ClassWriter(reader,ClassWriter.COMPUTE_MAXS);
          reader.accept(new ClassVisitor(Opcodes.ASM9,writer) {
            @Override public MethodVisitor visitMethod(int access,String method,String descriptor,String signature,String[] exceptions) {
              return new MethodVisitor(Opcodes.ASM9,super.visitMethod(access,method,descriptor,signature,exceptions)) {
                @Override public void visitMethodInsn(int opcode,String target,String called,String desc,boolean iface) {
                  if(opcode==Opcodes.INVOKESTATIC && target.equals("java/util/UUID") && called.equals("randomUUID") && desc.equals("()Ljava/util/UUID;")) {
                    super.visitLdcInsn(name); super.visitMethodInsn(Opcodes.INVOKESTATIC,"research/alfio/ReplayEntropy","uuid","(Ljava/lang/String;)Ljava/util/UUID;",false); replacements[0]++; return;
                  }
                  if(opcode==Opcodes.INVOKESPECIAL && target.equals("java/util/Date") && called.equals("<init>") && desc.equals("()V")) {
                    super.visitLdcInsn(name); super.visitMethodInsn(Opcodes.INVOKESTATIC,"research/alfio/ReplayEntropy","millis","(Ljava/lang/String;)J",false);
                    super.visitMethodInsn(opcode,target,called,"(J)V",iface); replacements[1]++; return;
                  }
                  if(opcode==Opcodes.INVOKESTATIC && target.equals("java/lang/System") && called.equals("currentTimeMillis") && desc.equals("()J")) {
                    super.visitLdcInsn(name); super.visitMethodInsn(Opcodes.INVOKESTATIC,"research/alfio/ReplayEntropy","millis","(Ljava/lang/String;)J",false); replacements[2]++; return;
                  }
                  super.visitMethodInsn(opcode,target,called,desc,iface);
                }
              };
            }
          },0);
          if(Arrays.stream(replacements).sum()==0) return null;
          synchronized(ReplayEntropy.class) {
            transformed.put(name,Map.of("uuid_sites",replacements[0],"date_constructor_sites",replacements[1],"wall_clock_sites",replacements[2],
              "input_sha256",HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(bytes))));
          }
          return writer.toByteArray();
        } catch(Throwable error) {
          // A transformer exception can otherwise be silently ignored by the JVM.
          error.printStackTrace(); Runtime.getRuntime().halt(91); return null;
        }
      }
    });
  }
}
