package com.oltpbenchmark.api;

/** One-hop static parameter transfer checks; no application input restrictions. */
public class InterproceduralFixture {
  static void hit() {}
  static void sink(boolean parameter) { if (!parameter) hit(); }
  static void blocked(boolean enabled) { if (enabled) sink(enabled); }
  static void allowed(boolean enabled) { if (!enabled) sink(enabled); }
  static void literalBlocked() { sink(true); }
  static void literalAllowed() { sink(false); }
  static void two(boolean a, boolean b) { if (a) { if (!b) hit(); } }
  static void sameActual(boolean flag) { two(flag,flag); }
  static void distinctActual(boolean left, boolean right) { two(left,right); }
  static void stringSink(String renamed) { if (renamed.equals("west")) hit(); }
  static void stringBlocked(String mode) { if (mode.equals("east")) stringSink(mode); }
  static void changedActual(boolean enabled) { if (enabled) { enabled=false; sink(enabled); } }
  static void changedFormal(boolean p) { p=false; if(!p) hit(); }
  static void changedFormalCaller() { changedFormal(true); }
  static void varargs(boolean... flags) { hit(); }
  static void varargsCaller() { varargs(true); }
  void virtual(boolean flag) { if(!flag) hit(); }
  static void virtualCaller(InterproceduralFixture obj) { obj.virtual(true); }
  static Runnable deferred(boolean flag) { if(flag) return () -> sink(flag); return () -> {}; }
  static void recursive(boolean flag) { if(flag) recursive(false); if(!flag) hit(); }
  static void recursiveCaller() { recursive(true); }
  static void overload(boolean flag) { if(!flag) hit(); }
  static void overload(String text) { if(text.equals("yes")) hit(); }
  static void overloadCaller() { overload(true); overload("yes"); }
}
