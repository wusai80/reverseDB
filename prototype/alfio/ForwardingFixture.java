package research.alfio;

/** Synthetic semantic controls; no business input bounds or lowering claims. */
public interface ForwardingFixture {
    int sink(Object a,Object b,long count);
    default int forward(Object value,long count) { return sink(value,value,count); }
    default int delegate(Object value,long count) { return forward(value,count); }
    default int discarded(Object value,long count) { sink(value,value,count); return 7; }
    default int twice(Object value,long count) { sink(value,value,count); return sink(value,value,count); }
    default int branch(boolean flag,Object value,long count) { return flag ? sink(value,value,count) : 0; }
    default int catches(Object value,long count) {
        try { return sink(value,value,count); } catch(IllegalStateException failure) { return -1; }
    }
    default int external(Object value) { return value.hashCode(); }
    default int cast(Object value,long count) { return sink((String)value,value,count); }
    default int recursive(Object value,long count) { return recursive(value,count); }
}
