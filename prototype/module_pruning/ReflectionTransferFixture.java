package com.oltpbenchmark.api;

/** Targets and guards are analyzed; the reflection bridge is not executed. */
public class ReflectionTransferFixture {
  static void hit() {}
  static class Box {
    private void sink(boolean value) { if (!value) hit(); }
    private void text(String value) { if (value.equals("west")) hit(); }
    private void pair(boolean a, boolean b) { if(a) { if(!b) hit(); } }
    private void numeric(int value) { hit(); }
    private void widened(long value) { hit(); }
    private void mutable(boolean value) { value=false; if(!value) hit(); }
    private void varargs(String... values) { hit(); }
  }
  static class Ambiguous { void sink(boolean a){} void sink(Boolean a){} }
  static class Parent { void sink(boolean a){} }
  static class Child extends Parent {}
  static class Recursive { void again(boolean b) throws Exception { TpccApp.invoke(this,"again",b); } }
  static void blocked(boolean enabled) throws Exception { Box box=new Box(); if(enabled) TpccApp.invoke(box,"sink",enabled); }
  static void allowed(boolean enabled) throws Exception { Box box=new Box(); if(!enabled) TpccApp.invoke(box,"sink",enabled); }
  static void literal() throws Exception { Box box=new Box(); TpccApp.invoke(box,"sink",true); }
  static void text(String mode) throws Exception { Box box=new Box(); if(mode.equals("east")) TpccApp.invoke(box,"text",mode); }
  static void alias(boolean flag) throws Exception { Box box=new Box(); TpccApp.invoke(box,"pair",flag,flag); }
  static void ambiguous() throws Exception { Ambiguous box=new Ambiguous(); TpccApp.invoke(box,"sink",true); }
  static void packed(Object[] args) throws Exception { Box box=new Box(); TpccApp.invoke(box,"sink",args); }
  static void nullPack() throws Exception { Box box=new Box(); TpccApp.invoke(box,"sink",null); }
  static void wrongType() throws Exception { Box box=new Box(); TpccApp.invoke(box,"sink","true"); }
  static void unknownType(Object value) throws Exception { Box box=new Box(); TpccApp.invoke(box,"text",value); }
  static void dynamic(String name) throws Exception { Box box=new Box(); TpccApp.invoke(box,name,true); }
  static void inherited() throws Exception { Child box=new Child(); TpccApp.invoke(box,"sink",true); }
  static void receiverChanged() throws Exception { Box box=new Box(); box=new Box(); TpccApp.invoke(box,"sink",true); }
  static void targetVarargs() throws Exception { Box box=new Box(); TpccApp.invoke(box,"varargs",(Object)new String[]{"x"}); }
  static void numeric() throws Exception { Box box=new Box(); TpccApp.invoke(box,"numeric",1); }
  static void widening() throws Exception { Box box=new Box(); TpccApp.invoke(box,"widened",1); }
  static void mutable() throws Exception { Box box=new Box(); TpccApp.invoke(box,"mutable",true); }
  static void deferred(boolean value) { Box box=new Box(); if(value) { Runnable task=()->{try{TpccApp.invoke(box,"sink",value);}catch(Exception e){throw new RuntimeException(e);}}; } }
}
