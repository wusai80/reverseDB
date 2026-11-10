package com.oltpbenchmark.api;

import java.util.function.Supplier;

/** Static attribution fixtures, never executed as business workloads. */
public class InitializationFixture {
  static class Base { Base(int value) {} }
  static class Box extends Base {
    static Object marker = new Object();
    Object instance = new Object();
    static { touch(); }
    { touch(); }
    Box() { this(1); }
    Box(int value) { super(value); }
    static void touch() {}
  }
  static class Dormant {
    Object instance = new Object();
    Dormant() {}
  }
  static class Implicit { Object instance = new Object(); }
  static Object allocate() { return new Box(); }
  static Object overloaded() { return new Box(2); }
  static Object implicit() { return new Implicit(); }
  static Supplier<Box> deferred() { return Box::new; }
  static Object anonymous() { return new Box() { Object nested = new Object(); }; }
  static Object local() {
    class Local { Object nested = new Object(); }
    return new Local();
  }
}
