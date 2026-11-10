package research.alfio;

import java.nio.file.*;
import java.security.*;
import java.util.*;
import org.objectweb.asm.*;
import org.objectweb.asm.tree.*;
import com.fasterxml.jackson.databind.ObjectMapper;

/** Bytecode facts only: calls, lambda handles, branch targets and SQL annotations.
 * Does not infer semantic equivalence, dynamic proxy dispatch or deletability.
 */
public final class AlfioDependencyExtract {
  static Object value(Object value) {
    if(value instanceof Type type) return Map.of("class",type.getClassName());
    if(value instanceof AnnotationNode annotation) return annotation(annotation);
    if(value instanceof String[] pair) return List.of(pair);
    if(value instanceof List<?> list) return list.stream().map(AlfioDependencyExtract::value).toList();
    return value;
  }
  static Map<String,Object> annotation(AnnotationNode annotation) {
    Map<String,Object> map=new LinkedHashMap<>(); map.put("type",annotation.desc);
    Map<String,Object> values=new LinkedHashMap<>();
    if(annotation.values!=null) for(int i=0;i<annotation.values.size();i+=2) values.put((String)annotation.values.get(i),value(annotation.values.get(i+1)));
    map.put("values",values); return map;
  }
  static List<Map<String,Object>> annotations(List<AnnotationNode> values) { return values==null?List.of():values.stream().map(AlfioDependencyExtract::annotation).toList(); }
  public static void main(String[] args) throws Exception {
    Path root=Path.of(args[0]); List<Map<String,Object>> classes=new ArrayList<>();
    Set<String> bodies=Set.of("alfio/manager/TicketReservationManager","alfio/manager/EventReservationManager","alfio/manager/WaitingQueueSubscriptionProcessor",
      "alfio/config/DataSourceConfiguration","alfio/config/DataSourceConfiguration$CustomDataSourceTransactionManager","alfio/config/RoleAndOrganizationsTransactionPreparer");
    try(var files=Files.walk(root.resolve("alfio"))) {
      for(Path path:files.filter(p->p.toString().endsWith(".class")).sorted().toList()) {
        String relative=root.relativize(path).toString().replace('\\','/'); String name=relative.substring(0,relative.length()-6);
        if(!name.startsWith("alfio/repository/") && !bodies.contains(name)) continue;
        byte[] bytes=Files.readAllBytes(path); ClassNode node=new ClassNode(); new ClassReader(bytes).accept(node,0);
        List<Map<String,Object>> methods=new ArrayList<>();
        for(MethodNode method:node.methods) {
          Map<String,Object> m=new LinkedHashMap<>(); m.put("id",name+"#"+method.name+method.desc); m.put("name",method.name); m.put("descriptor",method.desc);
          m.put("annotations",annotations(method.visibleAnnotations)); m.put("access",method.access);
          List<Object> parameters=new ArrayList<>();
          if(method.visibleParameterAnnotations!=null) for(int i=0;i<method.visibleParameterAnnotations.length;i++) parameters.add(Map.of("position",i,"annotations",annotations(method.visibleParameterAnnotations[i])));
          m.put("parameters",parameters);
          List<Map<String,Object>> calls=new ArrayList<>(), branches=new ArrayList<>(); int line=-1;
          for(int i=0;i<method.instructions.size();i++) {
            var instruction=method.instructions.get(i);
            if(instruction instanceof LineNumberNode location) line=location.line;
            if(instruction instanceof MethodInsnNode call) calls.add(Map.of("index",i,"line",line,"opcode",call.getOpcode(),"kind","direct_call","target",call.owner+"#"+call.name+call.desc));
            if(instruction instanceof InvokeDynamicInsnNode dynamic) for(Object argument:dynamic.bsmArgs) if(argument instanceof Handle handle) {
              calls.add(Map.of("index",i,"line",line,"kind","bootstrap_handle","bootstrap",dynamic.bsm.getOwner()+"#"+dynamic.bsm.getName(),"target",handle.getOwner()+"#"+handle.getName()+handle.getDesc()));
            }
            if(instruction instanceof JumpInsnNode branch) branches.add(Map.of("index",i,"line",line,"opcode",branch.getOpcode(),"target_index",method.instructions.indexOf(branch.label)));
          }
          m.put("calls",calls); m.put("branch_targets",branches); methods.add(m);
        }
        classes.add(Map.of("class",name,"sha256",HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(bytes)),"annotations",annotations(node.visibleAnnotations),"methods",methods));
      }
    }
    new ObjectMapper().writerWithDefaultPrettyPrinter().writeValue(Path.of(args[1]).toFile(),Map.of("classes",classes,"scope","bounded binary call/annotation facts; no automatic effect or deletion proof"));
  }
}
