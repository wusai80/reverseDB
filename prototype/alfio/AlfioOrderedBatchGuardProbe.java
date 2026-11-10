package research.alfio;

import java.nio.file.*;
import java.sql.*;
import java.util.*;
import alfio.model.PriceContainer.VatStatus;
import alfio.util.Json;
import org.springframework.jdbc.core.namedparam.*;

/** Guard decisions execute a no-match UPDATE, never remove or rewrite fixture data. */
public class AlfioOrderedBatchGuardProbe {
  public static void main(String[] args) throws Exception {
    List<Object> results=new ArrayList<>();
    try(Connection c=DriverManager.getConnection(args[0],"alfio_user",System.getenv("ALFIO_DB_PASSWORD"))) {
      var initial=AlfioFusionContractProbe.snapshot(c);
      for(String test:List.of("disabled","custom-template","unknown-sql","empty","unknown-parameter-shape","unknown-binding","autocommit")) {
        System.setProperty(AlfioOrderedBatch.ENABLE,"true");
        var capture=new AlfioFusionContractProbe.Capture(new AlfioFusionContractProbe.Wire(null),false);
        AlfioFusionContractProbe.repository(capture).reserveTickets(null,Arrays.asList((Integer)null),AlfioFusionContractProbe.category(1),null,VatStatus.NONE,i->null);
        var params=capture.params;
        var wire=new AlfioFusionContractProbe.Wire(c);
        NamedParameterJdbcTemplate template=new NamedParameterJdbcTemplate(wire);
        String sql=AlfioOrderedBatch.SQL;
        switch(test) {
          case "disabled" -> System.setProperty(AlfioOrderedBatch.ENABLE,"false");
          case "custom-template" -> template=new NamedParameterJdbcTemplate(wire) {
            @Override public int[] batchUpdate(String q,SqlParameterSource[] p) { return new int[]{0}; }
          };
          case "unknown-sql" -> sql+=" ";
          case "empty" -> params=new SqlParameterSource[0];
          case "unknown-parameter-shape" -> ((MapSqlParameterSource)params[0]).addValue("unused",1);
          case "unknown-binding" -> ((MapSqlParameterSource)params[0]).registerSqlType("id",Types.INTEGER);
          default -> { }
        }
        int before=AlfioOrderedBatch.snapshot().size();
        int[] counts=AlfioOrderedBatch.execute(template,sql,params);
        var events=AlfioOrderedBatch.snapshot().subList(before,AlfioOrderedBatch.snapshot().size());
        if(events.size()!=1||!events.get(0).get("path").equals("fallback:"+test))throw new AssertionError(test+events);
        if(Arrays.stream(counts).sum()!=0||!AlfioFusionContractProbe.snapshot(c).equals(initial))throw new AssertionError(test);
        results.add(Map.of("case",test,"dispatch",events,"counts",counts,"stateUnchanged",true,"wire",wire.events));
      }
    }
    Files.writeString(Path.of(args[1]),Json.toJson(results));
  }
}
