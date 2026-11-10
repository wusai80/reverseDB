package com.oltpbenchmark.api;

import java.sql.SQLException;

/** Independent syntax/analysis-boundary fixtures, not additional business workloads. */
public final class DependencyFixture {
  public static class RenderBox { public String render(int id) { return "item"+id; } }
  public static class AmbiguousBox {
    public String render(int id) { return "primitive"; }
    public String render(Integer id) { return "boxed"; }
  }
  public SQLStmt simple = new SQLStmt("SELECT id FROM records WHERE id = ?");
  public SQLStmt hiddenFunction = new SQLStmt("SELECT change_state()");
  public SQLStmt writing = new SQLStmt("UPDATE records SET id = ? WHERE id = ?");
  public SQLStmt dynamic = new SQLStmt(sqlFromEnvironment());
  public SQLStmt literalPercent = new SQLStmt("SELECT %s FROM %s".formatted("%s","records"));
  static String sqlFromEnvironment() { return System.getProperty("research.query"); }

  static void renamedDiscard() throws Exception {
    RenderBox box = new RenderBox();
    TpccApp.invoke(box,"render",1);
  }
  static String retainedReturn() throws Exception {
    RenderBox box = new RenderBox();
    String result=(String)TpccApp.invoke(box,"render",1);
    return result;
  }
  static void alias() throws Exception {
    RenderBox box = new RenderBox(); RenderBox another=box;
    TpccApp.invoke(another,"render",1);
  }
  static void dynamicName(String method) throws Exception {
    RenderBox box = new RenderBox(); TpccApp.invoke(box,method,1);
  }
  static void reassigned() throws Exception {
    RenderBox box = new RenderBox(); box = new RenderBox();
    TpccApp.invoke(box,"render",1);
  }
  static void unknownReceiver(RenderBox box) throws Exception {
    TpccApp.invoke(box,"render",1);
  }
  static void packedArgs(Object[] values) throws Exception {
    RenderBox box = new RenderBox(); TpccApp.invoke(box,"render",values);
  }
  static void ambiguous() throws Exception {
    AmbiguousBox box = new AmbiguousBox(); TpccApp.invoke(box,"render",1);
  }
  static void anonymous() throws Exception {
    RenderBox box = new RenderBox() { @Override public String render(int id) { return "override"; } };
    TpccApp.invoke(box,"render",1);
  }
  static int checkedFailure() throws SQLException { throw new SQLException("fixture"); }
  static void droppedButThrows() throws SQLException { checkedFailure(); }

  public static class OtherBox { public String render(int id) { return "other"+id; } }
  static void branchEast(String region) throws Exception {
    Object box=switch(region) { case "east" -> new RenderBox(); default -> new OtherBox(); };
    switch(region) { case "east" -> TpccApp.invoke(box,"render",1); default -> {} }
  }
  static void branchWest(String region) throws Exception {
    Object box=switch(region) { case "east" -> new RenderBox(); default -> new OtherBox(); };
    switch(region) { case "west" -> TpccApp.invoke(box,"render",1); default -> {} }
  }
  static void selectorChanged(String region) throws Exception {
    Object box=switch(region) { case "east" -> new RenderBox(); default -> new OtherBox(); };
    region="west";
    switch(region) { case "west" -> TpccApp.invoke(box,"render",1); default -> {} }
  }
  static void differentSelector(String region,String destination) throws Exception {
    Object box=switch(region) { case "east" -> new RenderBox(); default -> new OtherBox(); };
    switch(destination) { case "east" -> TpccApp.invoke(box,"render",1); default -> {} }
  }
  static void fallthrough(String region) throws Exception {
    Object box=switch(region) { case "east" -> new RenderBox(); default -> new OtherBox(); };
    switch(region) { case "east": case "west": TpccApp.invoke(box,"render",1); break; default: break; }
  }
  static void receiverChanged(String region) throws Exception {
    Object box=switch(region) { case "east" -> new RenderBox(); default -> new OtherBox(); };
    box=new OtherBox();
    switch(region) { case "east" -> TpccApp.invoke(box,"render",1); default -> {} }
  }
  static Runnable deferred(String region) {
    Object box=switch(region) { case "east" -> new RenderBox(); default -> new OtherBox(); };
    switch(region) {
      case "east" -> { return () -> { try { TpccApp.invoke(box,"render",1); } catch(Exception e) { throw new RuntimeException(e); } }; }
      default -> { return () -> {}; }
    }
  }
  static void multipleLabels(String region) throws Exception {
    Object box=switch(region) { case "east" -> new RenderBox(); default -> new OtherBox(); };
    switch(region) { case "east","west" -> TpccApp.invoke(box,"render",1); default -> {} }
  }
  static void consumerDefault(String region) throws Exception {
    Object box=switch(region) { case "east" -> new RenderBox(); default -> new OtherBox(); };
    switch(region) { case "west" -> {} default -> TpccApp.invoke(box,"render",1); }
  }
}
