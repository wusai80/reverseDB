package research.alfio;

import java.nio.file.*;
import java.security.MessageDigest;
import java.util.*;
import org.objectweb.asm.*;
import org.objectweb.asm.tree.*;
import com.fasterxml.jackson.databind.ObjectMapper;

/** Lossless-for-supported-opcodes input to a conservative forwarding analyzer.
 * Unsupported instructions remain explicit; no method is silently shortened. */
public final class ForwardingBytecodeExtract {
    public static void main(String[] args) throws Exception {
        List<Object> classes=new ArrayList<>();
        for(int a=1;a<args.length;a++) {
            byte[] bytes=Files.readAllBytes(Path.of(args[a]));
            ClassNode cls=new ClassNode();new ClassReader(bytes).accept(cls,0);
            List<Object> methods=new ArrayList<>();
            for(MethodNode method:cls.methods) {
                List<Object> parameters=new ArrayList<>(), instructions=new ArrayList<>();
                int slot=(method.access & Opcodes.ACC_STATIC)==0 ? 1 : 0, position=0;
                for(Type type:Type.getArgumentTypes(method.desc)) {
                    parameters.add(Map.of("position",position++,"slot",slot,"type",type.getDescriptor(),"size",type.getSize()));
                    slot+=type.getSize();
                }
                for(int i=0;i<method.instructions.size();i++) {
                    AbstractInsnNode insn=method.instructions.get(i);
                    if(insn.getOpcode()<0)continue; // labels, frames, debug lines carry no executable opcode
                    Map<String,Object> record=new LinkedHashMap<>();
                    record.put("index",i);record.put("opcode",insn.getOpcode());
                    if(insn instanceof VarInsnNode var)record.put("slot",var.var);
                    if(insn instanceof IntInsnNode integer)record.put("operand",integer.operand);
                    if(insn instanceof TypeInsnNode type)record.put("type_operand",type.desc);
                    if(insn instanceof LdcInsnNode ldc) {
                        record.put("constant_kind",ldc.cst.getClass().getName());
                        if(ldc.cst instanceof String || ldc.cst instanceof Number)record.put("constant",ldc.cst);
                    }
                    if(insn instanceof MethodInsnNode call) {
                        record.put("target",call.owner+"#"+call.name+call.desc);
                        record.put("owner",call.owner);record.put("name",call.name);
                        record.put("argument_count",Type.getArgumentTypes(call.desc).length);
                        record.put("return_type",Type.getReturnType(call.desc).getDescriptor());
                    }
                    if(insn instanceof InvokeDynamicInsnNode dynamic) {
                        List<Object> handles=new ArrayList<>();
                        for(Object argument:dynamic.bsmArgs) if(argument instanceof Handle handle)
                            handles.add(Map.of("target",handle.getOwner()+"#"+handle.getName()+handle.getDesc(),"tag",handle.getTag()));
                        record.put("bootstrap",dynamic.bsm.getOwner()+"#"+dynamic.bsm.getName());
                        record.put("bootstrap_handles",handles);
                    }
                    instructions.add(record);
                }
                methods.add(Map.of("id",cls.name+"#"+method.name+method.desc,"owner",cls.name,
                    "access",method.access,"owner_access",cls.access,"parameters",parameters,"instructions",instructions,
                    "return_type",Type.getReturnType(method.desc).getDescriptor(),
                    "exception_handlers",method.tryCatchBlocks.size()));
            }
            classes.add(Map.of("class",cls.name,"sha256",HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(bytes)),"methods",methods));
        }
        new ObjectMapper().writerWithDefaultPrettyPrinter().writeValue(Path.of(args[0]).toFile(),Map.of("classes",classes));
    }
}
