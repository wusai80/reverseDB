package research.alfio;

import java.io.*;
import java.nio.file.*;
import java.security.*;
import java.util.*;
import java.util.jar.*;
import java.util.zip.GZIPOutputStream;
import org.objectweb.asm.*;
import org.objectweb.asm.tree.*;
import com.fasterxml.jackson.databind.ObjectMapper;

/** Offline structural facts. No class loading, invocation, optimization or deletion. */
public final class BytecodeStructureIndex {
  static final ObjectMapper JSON=new ObjectMapper();
  static Object value(Object v) {
    if(v instanceof Type t)return Map.of("class",t.getClassName());
    if(v instanceof AnnotationNode a)return annotation(a);
    if(v instanceof String[] a)return List.of(a);
    if(v instanceof List<?> a)return a.stream().map(BytecodeStructureIndex::value).toList();
    return v;
  }
  static Map<String,Object> annotation(AnnotationNode a) {
    Map<String,Object> values=new TreeMap<>();
    if(a.values!=null)for(int i=0;i<a.values.size();i+=2)values.put((String)a.values.get(i),value(a.values.get(i+1)));
    return Map.of("type",a.desc,"values",values);
  }
  static List<Object> annotations(List<AnnotationNode> visible,List<AnnotationNode> invisible) {
    List<Object> result=new ArrayList<>();
    if(visible!=null)for(var a:visible)result.add(Map.of("visibility","runtime","annotation",annotation(a)));
    if(invisible!=null)for(var a:invisible)result.add(Map.of("visibility","classfile","annotation",annotation(a)));
    return result;
  }
  static Map<String,Object> handle(Handle h) {
    return Map.of("target",h.getOwner()+"#"+h.getName()+h.getDesc(),"tag",h.getTag());
  }
  static void emit(byte[] bytes,String source,String entry,boolean body,BufferedWriter writer) throws Exception {
    ClassNode c=new ClassNode();new ClassReader(bytes).accept(c,body?0:ClassReader.SKIP_CODE|ClassReader.SKIP_DEBUG|ClassReader.SKIP_FRAMES);
    List<Object> methods=new ArrayList<>();
    for(MethodNode m:c.methods) {
      Map<String,Object> method=new LinkedHashMap<>();
      method.put("id",c.name+"#"+m.name+m.desc);method.put("access",m.access);
      method.put("annotations",annotations(m.visibleAnnotations,m.invisibleAnnotations));
      method.put("exceptions",m.exceptions);method.put("body_indexed",body);
      if(body) {
        List<Object> calls=new ArrayList<>(),fields=new ArrayList<>(),types=new ArrayList<>(),branches=new ArrayList<>(),handlers=new ArrayList<>();
        int line=-1;
        for(int i=0;i<m.instructions.size();i++) {
          var n=m.instructions.get(i);
          if(n instanceof LineNumberNode l)line=l.line;
          if(n instanceof MethodInsnNode v)calls.add(Map.of("index",i,"line",line,"opcode",v.getOpcode(),"kind","invoke","target",v.owner+"#"+v.name+v.desc));
          if(n instanceof InvokeDynamicInsnNode v) {
            List<Object> handles=new ArrayList<>();
            for(Object a:v.bsmArgs)if(a instanceof Handle h)handles.add(handle(h));
            calls.add(Map.of("index",i,"line",line,"kind","invokedynamic","name",v.name,"descriptor",v.desc,"bootstrap",handle(v.bsm),"handles",handles));
          }
          if(n instanceof FieldInsnNode v)fields.add(Map.of("index",i,"opcode",v.getOpcode(),"owner",v.owner,"name",v.name,"descriptor",v.desc));
          if(n instanceof TypeInsnNode v)types.add(Map.of("index",i,"opcode",v.getOpcode(),"type",v.desc));
          if(n instanceof LdcInsnNode v && (v.cst instanceof Type || v.cst instanceof Handle || v.cst instanceof ConstantDynamic))
            types.add(Map.of("index",i,"kind","symbolic_constant","value",v.cst.toString()));
          if(n instanceof JumpInsnNode v)branches.add(Map.of("index",i,"opcode",v.getOpcode(),"targets",List.of(m.instructions.indexOf(v.label))));
          if(n instanceof TableSwitchInsnNode v) {
            List<Integer> targets=new ArrayList<>();targets.add(m.instructions.indexOf(v.dflt));for(var l:v.labels)targets.add(m.instructions.indexOf(l));
            branches.add(Map.of("index",i,"opcode",v.getOpcode(),"targets",targets));
          }
          if(n instanceof LookupSwitchInsnNode v) {
            List<Integer> targets=new ArrayList<>();targets.add(m.instructions.indexOf(v.dflt));for(var l:v.labels)targets.add(m.instructions.indexOf(l));
            branches.add(Map.of("index",i,"opcode",v.getOpcode(),"targets",targets));
          }
        }
        for(var h:m.tryCatchBlocks)handlers.add(Map.of("start",m.instructions.indexOf(h.start),"end",m.instructions.indexOf(h.end),"handler",m.instructions.indexOf(h.handler),"type",h.type==null?"<finally>":h.type));
        method.put("calls",calls);method.put("fields",fields);method.put("types",types);method.put("branches",branches);method.put("handlers",handlers);
      }
      methods.add(method);
    }
    List<Object> fields=new ArrayList<>();for(var f:c.fields)fields.add(Map.of("name",f.name,"descriptor",f.desc,"access",f.access,"annotations",annotations(f.visibleAnnotations,f.invisibleAnnotations)));
    Map<String,Object> result=new LinkedHashMap<>();
    result.put("class",c.name);result.put("super",c.superName);result.put("interfaces",c.interfaces);result.put("access",c.access);
    result.put("source",source);result.put("entry",entry);result.put("application",body);
    result.put("sha256",HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(bytes)));
    result.put("annotations",annotations(c.visibleAnnotations,c.invisibleAnnotations));result.put("fields",fields);result.put("methods",methods);
    writer.write(JSON.writeValueAsString(result));writer.newLine();
  }
  public static void main(String[] args) throws Exception {
    // Config order is actual classpath precedence. Multi-release JAR view fixed to Java 17.
    List<Map<String,String>> inputs=JSON.readValue(Path.of(args[0]).toFile(),List.class);
    try(var writer=new BufferedWriter(new OutputStreamWriter(new GZIPOutputStream(Files.newOutputStream(Path.of(args[1]))),java.nio.charset.StandardCharsets.UTF_8))) {
      for(var input:inputs) {
        Path path=Path.of(input.get("path"));boolean body=input.get("scope").equals("application");
        if(Files.isDirectory(path)) {
          try(var files=Files.walk(path)) {for(var p:files.filter(f->f.toString().endsWith(".class")).sorted().toList())emit(Files.readAllBytes(p),path.toString(),path.relativize(p).toString(),body,writer);}
        } else try(var jar=new JarFile(path.toFile(),false,JarFile.OPEN_READ,Runtime.Version.parse("17"))) {
          for(var entry:jar.versionedStream().filter(e->e.getName().endsWith(".class")&&!e.getName().startsWith("META-INF/")).sorted(Comparator.comparing(JarEntry::getName)).toList())
            try(var stream=jar.getInputStream(entry)) {emit(stream.readAllBytes(),path.toString(),entry.getRealName(),body,writer);}
        }
      }
    }
  }
}
