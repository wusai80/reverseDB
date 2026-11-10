// Recover selected native functions from the imported binary, without source input.
// @category ReverseDB
import ghidra.app.script.GhidraScript;
import ghidra.app.decompiler.DecompInterface;
import ghidra.app.decompiler.DecompileResults;
import ghidra.program.model.listing.Function;
import ghidra.program.model.listing.FunctionIterator;
import ghidra.program.model.listing.InstructionIterator;
import java.io.File;
import java.nio.file.Files;
import java.nio.charset.StandardCharsets;
import java.util.*;
import com.google.gson.GsonBuilder;

public class RecoverFossil extends GhidraScript {
    public void run() throws Exception {
        File out = new File(getScriptArgs()[0]); out.mkdirs();
        Set<String> wanted = new HashSet<>(Arrays.asList(
            "timeline_cmd", "print_timeline", "timeline_query_for_tty",
            "wiki_to_text_sqlfunc", "wiki_convert_flags", "wiki_convert",
            "html_to_plaintext", "db_step", "db_column_text", "db_column_int"));
        DecompInterface decompiler = new DecompInterface();
        decompiler.openProgram(currentProgram);
        List<Map<String,Object>> records = new ArrayList<>();
        FunctionIterator functions = currentProgram.getFunctionManager().getFunctions(true);
        while(functions.hasNext()) {
            Function fn = functions.next();
            String name = fn.getName().replaceFirst("^_", "");
            if(!wanted.contains(name)) continue;
            Map<String,Object> record = new LinkedHashMap<>();
            record.put("name", name); record.put("address", fn.getEntryPoint().toString());
            record.put("body_bytes", fn.getBody().getNumAddresses());
            List<String> callees = new ArrayList<>();
            for(Function target: fn.getCalledFunctions(monitor)) callees.add(target.getName());
            Collections.sort(callees); record.put("direct_callees", callees);
            DecompileResults result = decompiler.decompileFunction(fn, 45, monitor);
            record.put("completed", result.decompileCompleted());
            record.put("error", result.getErrorMessage());
            if(result.decompileCompleted()) {
                Files.write(new File(out,name+".c").toPath(), result.getDecompiledFunction().getC().getBytes(StandardCharsets.UTF_8));
            }
            StringBuilder assembly = new StringBuilder();
            InstructionIterator instructions = currentProgram.getListing().getInstructions(fn.getBody(), true);
            while(instructions.hasNext()) {
                var instruction = instructions.next();
                assembly.append(instruction.getAddress()).append(" ").append(instruction).append("\n");
            }
            Files.write(new File(out,name+".asm").toPath(), assembly.toString().getBytes(StandardCharsets.UTF_8));
            records.add(record);
        }
        decompiler.dispose();
        Map<String,Object> report = new LinkedHashMap<>();
        report.put("program", currentProgram.getName());
        report.put("language", currentProgram.getLanguageID().toString());
        report.put("binary_sha256", currentProgram.getExecutableSHA256());
        report.put("requested", wanted); report.put("functions", records);
        report.put("source_files_used", false);
        report.put("symbol_assisted", true);
        report.put("recompiled_equivalence", false);
        Files.write(new File(out,"recovery.json").toPath(), new GsonBuilder().setPrettyPrinting().create().toJson(report).getBytes(StandardCharsets.UTF_8));
        println("Recovered " + records.size() + " selected functions");
    }
}
