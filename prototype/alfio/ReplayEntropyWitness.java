package alfio.research;

import java.util.*;
import research.alfio.ReplayEntropy;

public final class ReplayEntropyWitness {
  public static void main(String[] args) {
    ReplayEntropy.begin("control");
    UUID first=UUID.randomUUID(),second=UUID.randomUUID(); long date=new Date().getTime();
    var a=ReplayEntropy.finish();
    ReplayEntropy.begin("control");
    UUID again=UUID.randomUUID(),next=UUID.randomUUID(); long replayDate=new Date().getTime();
    var b=ReplayEntropy.finish();
    if(!first.equals(again)||!second.equals(next)||first.equals(second)||first.version()!=4||first.variant()!=2||date!=1793959200000L||date!=replayDate)
      throw new AssertionError("Entropy replay witness failed");
    if(ReplayEntropy.transformations().get("alfio/research/ReplayEntropyWitness")==null) throw new AssertionError("Missing instrumentation");
    System.out.println("PASS: seeded UUID identity/distinctness/version and Date constructor replay; no result rewriting");
  }
}
