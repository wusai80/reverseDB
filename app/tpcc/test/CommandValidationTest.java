package com.oltpbenchmark.api;

import org.json.*;

/** Contract rejection checks without a database; valid requests are covered by real integration. */
public class CommandValidationTest {
  static JSONObject valid() {
    return new JSONObject("""
      {"id":"test","type":"NewOrder","terminal":1,"warehouse":1,"district":1,
       "customer":1,"clock_ms":1793664000000,"lines":[
       {"item":1,"quantity":1},{"item":2,"quantity":1},{"item":3,"quantity":1},
       {"item":4,"quantity":1},{"item":5,"quantity":1}]}
      """);
  }
  static void rejected(JSONObject command) {
    try { TpccApp.validate(command); }
    catch (IllegalArgumentException | JSONException expected) { return; }
    throw new AssertionError("Invalid command accepted");
  }
  public static void main(String[] args) {
    TpccApp.validate(valid());
    rejected(valid().put("seed",42));
    rejected(valid().put("district",11));
    rejected(valid().put("terminal",0));
    rejected(valid().put("warehouse",2));
    rejected(valid().put("customer","1"));
    rejected(valid().put("clock_ms",-1));
    rejected(valid().put("type","Unsupported"));
    JSONObject c=valid(); c.getJSONArray("lines").getJSONObject(0).put("quantity",0); rejected(c);
    c=valid(); c.getJSONArray("lines").put(0,"invalid-line"); rejected(c);
    c=valid(); c.getJSONArray("lines").getJSONObject(0).put("item",1.5); rejected(c);
    c=valid(); c.remove("district"); rejected(c);
    System.out.println("1 valid and 11 invalid input contract cases passed");
  }
}
