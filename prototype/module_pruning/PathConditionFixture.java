package com.oltpbenchmark.api;

/** Static guard checks. Values are not a business-data whitelist. */
public class PathConditionFixture {
  static void hit() {}
  static void opposite(boolean enabled) { if (enabled) { if (!enabled) hit(); } }
  static void alternative(boolean enabled) { if (enabled) hit(); else hit(); }
  static void stringConflict(String mode) { if (mode.equals("east")) { if (mode.equals("west")) hit(); } }
  static void renamed(String unrelatedName) { if ("east".equals(unrelatedName)) hit(); }
  static void changed(boolean enabled) { if (enabled) { enabled=false; if (!enabled) hit(); } }
  static void changedLater(boolean enabled) { if (enabled) hit(); enabled=false; }
  boolean mutable;
  void field() { if (mutable) { if (!mutable) hit(); } }
  static void fallthrough(String mode) { switch(mode) { case "east": case "west": hit(); break; default: break; } }
  static void multi(String mode) { switch(mode) { case "east", "west" -> hit(); default -> hit(); } }
  static void switchConflict(String mode) { switch(mode) { case "east" -> { if (mode.equals("west")) hit(); } default -> {} } }
  static Runnable deferred(boolean enabled) { if (enabled) return () -> { if (!enabled) hit(); }; return () -> {}; }
  static void loop(boolean enabled) { while(enabled) { hit(); break; } }
  static void nestedUnknown(boolean enabled, int count) { if (enabled) { if (!enabled) { if (count>0) hit(); } } }
  static void identity(String mode) { if (mode=="east") hit(); }
  static void catchBranch() { try { throw new IllegalStateException(); } catch(Exception e) { hit(); } }
}
