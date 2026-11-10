"""Two source-bound SQL regions around unchanged Java Payment semantics.

Backend rules and typed boundary contracts are explicit/manual. No generic
Java-object, float arithmetic or UTF-16 lowering is enabled.
"""
from payment_fusion_transfer import extract
from fusion_regions import analyze, TYPES
from generate_neworder_fusion import bind
from fusion_placement import choose_placement
from fusion_placement_contracts import payment_contract


def generate(source, adapter, facts):
    ir=extract(source,adapter,facts)
    methods={x['method']:x for x in ir['call_sites']}
    params=dict(w_id='p_w',districtID='p_d',customerDistrictID='p_cd',customerWarehouseID='p_w',
                paymentAmount='p_amount',h_data='p_history',**{
        'BigDecimal.valueOf(paymentAmount)':'p_numeric','c.c_id':'p_c','c.c_balance':'p_balance',
        'c.c_ytd_payment':'p_ytd','c.c_payment_cnt':'p_count','c.c_data':'p_data',
        'new Timestamp(System.currentTimeMillis())':'p_time'})
    def sql(method):
        entry=methods[method]
        ordered=sorted(entry['bindings'],key=lambda b:b['index'])
        if any(b['expression'] not in params for b in ordered): raise ValueError('Unknown binding '+method)
        return bind(ir['sql'][entry['field']]['sql'],[params[b['expression']] for b in ordered])
    def select(method,target): return sql(method).replace(' FROM ',f' INTO {target} FROM ',1)+';'
    prefix=f'''CREATE SCHEMA reversedb_payment;
CREATE FUNCTION reversedb_payment.begin_payment(p_w integer,p_d integer,p_numeric numeric)
RETURNS TABLE(warehouse_name text,district_name text) LANGUAGE plpgsql VOLATILE
SET search_path=public,pg_catalog AS $body$
DECLARE v_w record; v_d record; v_rows bigint;
BEGIN
{sql('updateWarehouse')};
GET DIAGNOSTICS v_rows=ROW_COUNT;
IF v_rows=0 THEN RAISE EXCEPTION USING ERRCODE='RDB02',MESSAGE='W_ID='||p_w||' not found!'; END IF;
{select('getWarehouse','v_w')}
IF NOT FOUND THEN RAISE EXCEPTION USING ERRCODE='RDB02',MESSAGE='W_ID='||p_w||' not found!'; END IF;
{sql('updateDistrict')};
GET DIAGNOSTICS v_rows=ROW_COUNT;
IF v_rows=0 THEN RAISE EXCEPTION USING ERRCODE='RDB02',MESSAGE='D_ID='||p_d||' D_W_ID='||p_w||' not found!'; END IF;
{select('getDistrict','v_d')}
IF NOT FOUND THEN RAISE EXCEPTION USING ERRCODE='RDB02',MESSAGE='D_ID='||p_d||' D_W_ID='||p_w||' not found!'; END IF;
RETURN QUERY SELECT v_w.w_name::text,v_d.d_name::text;
END;
$body$;
'''
    result=bind(ir['result_query'],['p_cd','p_c'])
    def finish(fault=False):
        name='finish_payment_fault' if fault else 'finish_payment'
        failure="RAISE EXCEPTION USING ERRCODE='58000',MESSAGE='injected failure after writes';" if fault else ''
        return f'''CREATE FUNCTION reversedb_payment.{name}(p_w integer,p_d integer,p_cd integer,p_c integer,
p_balance double precision,p_ytd double precision,p_count integer,p_data text,p_bad boolean,
p_time timestamp,p_amount double precision,p_history text)
RETURNS jsonb LANGUAGE plpgsql VOLATILE SET search_path=public,pg_catalog AS $body$
DECLARE v_rows bigint; v_result jsonb;
BEGIN
IF p_bad THEN
{sql('updateBalanceCData')};
GET DIAGNOSTICS v_rows=ROW_COUNT;
IF v_rows=0 THEN RAISE EXCEPTION USING ERRCODE='RDB02',MESSAGE='Error in PYMNT Txn updating Customer C_ID='||p_c||' C_W_ID='||p_w||' C_D_ID='||p_cd; END IF;
ELSE
{sql('updateBalance')};
GET DIAGNOSTICS v_rows=ROW_COUNT;
IF v_rows=0 THEN RAISE EXCEPTION USING ERRCODE='RDB02',MESSAGE='C_ID='||p_c||' C_W_ID='||p_w||' C_D_ID='||p_cd||' not found!'; END IF;
END IF;
{sql('insertHistory')};
{failure}
SELECT to_jsonb(r) INTO v_result FROM ({result}) r;
RETURN jsonb_build_object('customer',v_result);
END;
$body$;
'''
    # Copy the scalar operations verbatim; do not reconstruct them in SQL.
    start=adapter.index('        cust.c_balance-=amount;')
    arithmetic=adapter[start:adapter.index('\n',start)].strip()
    begin=source.index('      if (w_name.length() > 10) {',source.index('   private void insertHistory('))
    end=source.index('      try (PreparedStatement payInsertHist',begin)
    history=source[begin:end]
    helper=HELPER.replace('@ARITHMETIC@',arithmetic).replace('@HISTORY@',history)
    insertion='    if (type.equals("Payment")) return SegmentedPayment.execute(c,cmd);\n'
    candidate=adapter.replace('    TPCCProcedure p = switch(type)',insertion+'    TPCCProcedure p = switch(type)',1)
    if candidate==adapter: raise ValueError('Adapter insertion failed')
    # The old Payment case is now unreachable from reference(), but retained as
    # source, while its original Java helper class remains available and loaded.
    external=dict(w='int32',d='int32',cd='int32',c='int32',numeric='jdbc_numeric',amount='jdbc_float8',time='timestamp')
    nodes=[]
    def node(id,queries=(),uses=None,defines=None,host=False):
        nodes.append(dict(id=id,queries=list(queries),uses=uses or {},defines=defines or {},
                          placement='host' if host else 'database',body_known=True,
                          transaction=None if host else 'outer_serializable'))
    node('java_amount_conversion',host=True)
    node('updateWarehouse',[ir['sql'][methods['updateWarehouse']['field']]['sql']],uses=dict(w='int32',numeric='jdbc_numeric'))
    node('getWarehouse',[ir['sql'][methods['getWarehouse']['field']]['sql']],uses={'w':'int32'},defines={'w_name':'jdbc_text'})
    node('updateDistrict',[ir['sql'][methods['updateDistrict']['field']]['sql']],uses=dict(w='int32',d='int32',numeric='jdbc_numeric'))
    node('getDistrict',[ir['sql'][methods['getDistrict']['field']]['sql']],uses=dict(w='int32',d='int32'),defines={'d_name':'jdbc_text'})
    node('original_customer_and_credit_java',host=True,uses=dict(w_name='jdbc_text',d_name='jdbc_text',amount='jdbc_float8',cd='int32',c='int32'),
         defines=dict(balance='jdbc_float8',ytd='jdbc_float8',count='int32',data='jdbc_text',bad='jdbc_boolean',history='jdbc_text'))
    node('selected_customer_update',[ir['sql'][methods[m]['field']]['sql'] for m in ('updateBalanceCData','updateBalance')],
         uses=dict(w='int32',cd='int32',c='int32',balance='jdbc_float8',ytd='jdbc_float8',count='int32',data='jdbc_text',bad='jdbc_boolean'))
    node('insertHistory',[ir['sql'][methods['insertHistory']['field']]['sql']],uses=dict(w='int32',d='int32',cd='int32',c='int32',amount='jdbc_float8',time='timestamp',history='jdbc_text'))
    node('result',[ir['result_query']],uses=dict(cd='int32',c='int32'),defines={'result':'json'})
    node('outer_commit_or_rollback',host=True,uses={'result':'json'})
    allowed=TYPES|{'jdbc_numeric','jdbc_float8','jdbc_text','jdbc_boolean'}
    graph=analyze(nodes,external,supported_types=allowed)
    selected=choose_placement(payment_contract(graph,ir['source_sha256']))
    # This is an emitter compatibility check, not an optimizer input.
    if [r['nodes'] for r in selected['database_regions']]!=[['updateWarehouse','getWarehouse','updateDistrict','getDistrict'],['selected_customer_update','insertHistory','result']]:
        raise ValueError('Selected placement has no implemented Payment backend')
    graph['boundary_contract']={
        'jdbc_numeric':'BigDecimal.valueOf(float amount) computed in Java; setBigDecimal -> numeric',
        'jdbc_float8':'Java float result widened by setDouble; no arithmetic lowered',
        'jdbc_text':'Java getString/setString; original UTF-16 transformations retained',
        'jdbc_boolean':'Original Java cust.c_credit.equals("BC") selects exactly one SQL update',
        'transaction':'Both regions and intervening JDBC share one connection and original outer transaction',
        'limits':'Source-bound manual split/ABI; generic region scanning reused, no automatic cut discovery'}
    mapping=dict(source_sha256=ir['source_sha256'],adapter_sha256=ir['adapter_sha256'],
                 database_methods={m:methods[m] for m in ('updateWarehouse','getWarehouse','updateDistrict','getDistrict','updateBalanceCData','updateBalance','insertHistory')},
                 java_preserved=['original getCustomerById and TPCCUtil object materialization','original getCData including SELECT and UTF-16 substring'],
                 copied_arithmetic=arithmetic,copied_history=history,ir=ir)
    return dict(sql=prefix+finish()+finish(True),helper=helper,adapter=candidate,plan=graph,source_map=mapping,automatic_placement=selected)


HELPER='''package com.oltpbenchmark.api;
import java.math.BigDecimal;
import java.sql.*;
import org.json.*;
import org.postgresql.util.PSQLException;
import com.oltpbenchmark.benchmarks.tpcc.procedures.Payment;
import com.oltpbenchmark.benchmarks.tpcc.pojo.Customer;
import com.oltpbenchmark.types.DatabaseType;

/** Generated source-bound bridge; original Java numeric/string semantics stay here. */
public final class SegmentedPayment {
  static String historyData(String w_name,String d_name) {
@HISTORY@
    return h_data;
  }
  public static JSONObject execute(Connection c,JSONObject cmd) throws Exception {
    int d=cmd.getInt("district"), cd=cmd.getInt("customer_district"), customer=cmd.getInt("customer");
    float amount=cmd.getBigDecimal("amount").floatValue();
    Payment p=new Payment(); ((Procedure)p).initialize(DatabaseType.POSTGRES);
    try {
      String w_name,d_name;
      try (PreparedStatement s=c.prepareStatement("SELECT * FROM reversedb_payment.begin_payment(?,?,?)")) {
        s.setInt(1,1); s.setInt(2,d); s.setBigDecimal(3,BigDecimal.valueOf(amount));
        try(ResultSet rows=s.executeQuery()) {
          if(!rows.next()) throw new IllegalStateException("Missing Payment prefix result");
          w_name=rows.getString("warehouse_name"); d_name=rows.getString("district_name");
        }
      }
      Customer cust=(Customer)TpccApp.invoke(p,"getCustomerById",1,cd,customer,c);
      @ARITHMETIC@
      boolean bad=cust.c_credit.equals("BC");
      if(bad) cust.c_data=(String)TpccApp.invoke(p,"getCData",c,1,d,cd,1,amount,cust);
      String history=historyData(w_name,d_name);
      String function=Boolean.getBoolean("reversedb.payment.lateFault")?"finish_payment_fault":"finish_payment";
      try(PreparedStatement s=c.prepareStatement("SELECT reversedb_payment."+function+"(?,?,?,?,?,?,?,?,?,?::timestamp,?,?)")) {
        s.setInt(1,1); s.setInt(2,d); s.setInt(3,cd); s.setInt(4,cust.c_id);
        s.setDouble(5,cust.c_balance); s.setDouble(6,cust.c_ytd_payment); s.setInt(7,cust.c_payment_cnt);
        s.setString(8,cust.c_data); s.setBoolean(9,bad);
        // Keep the original driver timestamp codec, including infinity sentinels.
        s.setTimestamp(10,new Timestamp(cmd.getLong("clock_ms")));
        s.setDouble(11,amount); s.setString(12,history);
        try(ResultSet rows=s.executeQuery()) {
          if(!rows.next()) throw new IllegalStateException("Missing Payment finish result");
          return new JSONObject(rows.getString(1));
        }
      }
    } catch(PSQLException error) {
      if("RDB02".equals(error.getSQLState())) throw new RuntimeException(error.getServerErrorMessage().getMessage());
      throw error;
    }
  }
}
'''
