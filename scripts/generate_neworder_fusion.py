#!/usr/bin/env python3
"""Source-bound lowering for ONE recognized decompiled transaction, not a Java compiler.

SQL constants and JDBC bindings come from attributed extraction. Backend rules
are handwritten and explicit. Unknown source revisions fail closed.
"""
import hashlib
import json
import re
from pathlib import Path
from fusion_regions import require_neworder_region, schedule, validate_schedule
from fusion_placement import choose_placement
from fusion_placement_contracts import neworder_contract


def lower(source, adapter, facts, expected_source_hash, expected_adapter_hash):
    if hashlib.sha256(source.encode()).hexdigest() != expected_source_hash:
        raise ValueError('Unsupported NewOrder source revision')
    if hashlib.sha256(adapter.encode()).hexdigest() != expected_adapter_hash:
        raise ValueError('Unsupported adapter source revision')
    sql = {row['symbol'].split('#')[1]: dict(sql=' '.join(row['constant_sql'].split()), source=row['location'])
           for row in facts['sql_fields'] if '.NewOrder#' in row['symbol']}
    assert len(sql) == 10
    variables = dict(re.findall(r'PreparedStatement (\w+) = this\.getPreparedStatement\(conn, this\.(\w+)\)', source))
    assert set(variables.values()) == set(sql)
    bindings = {variable: {} for variable in variables}
    calls = [row for row in facts['calls'] if '.NewOrder#' in row['caller']]
    for call in calls:
        match = re.fullmatch(r'(\w+)\.set(Int|Double|String|Timestamp)\((\d+), (.*)\)',call['expression'],re.S)
        if match and match[1] in bindings:
            index=int(match[3]); assert index not in bindings[match[1]]
            bindings[match[1]][index]=dict(java_type=match[2],expression=match[4],source=call['location'])
    for variable,field in variables.items():
        assert set(bindings[variable]) == set(range(1,sql[field]['sql'].count('?')+1)), variable
        sql[field]['bindings']=[bindings[variable][i] for i in sorted(bindings[variable])]
    chain=[row['target_symbol'].split('#')[1].split('(')[0] for row in calls
           if '#newOrderTransaction(' in row['caller'] and '.NewOrder#' in row['target_symbol']]
    assert chain == ['getCustomer','getWarehouse','getDistrict','updateDistrict','insertOpenOrder','insertNewOrder','getItemPrice','getStock','getDistInfo'],chain
    batch=[row['expression'].split('.')[0] for row in calls if '#newOrderTransaction(' in row['caller'] and row['expression'].endswith('.executeBatch()')]
    assert batch == ['stmtInsertOrderLine','stmtUpdateStock']
    district_fields = re.findall(r'case (\d+) -> s\.(s_dist_\d+);',source)
    assert [int(n) for n,_ in district_fields] == list(range(1,11))
    threshold = re.search(r'if \(s.s_quantity - ol_quantity >= (\d+)\)',source)
    wrap = re.search(r's.s_quantity \+= -ol_quantity \+ (\d+);',source)
    assert threshold and wrap and 's.s_quantity -= ol_quantity;' in source
    assert 'float ol_amount = ol_quantity * i_price;' in source and 'return rs.getFloat("I_PRICE");' in source
    assert 'Arrays.fill(suppliers,1);' in adapter and 'invoke(p,"newOrderTransaction",1,d,customer,lines.size(),1,' in adapter
    branch=adapter[adapter.index('    if (type.equals("NewOrder")) {'):adapter.index('    TPCCProcedure p = switch(type)')]
    projections=re.findall(r'rows\(c,"([^"]+)"',branch)
    assert len(projections)==3 and projections[2].endswith('ORDER BY ol_number')
    return dict(kind='source-bound-NewOrder-IR/v1',source_sha256=expected_source_hash,adapter_sha256=expected_adapter_hash,
        sql=sql,helper_order=chain,batch_order=batch,adapter_queries=projections,
        stages=['adapter order-id read',*chain[:6],'read and stage each line','flush all order lines','flush all stock updates','complete order and lines projection'],
        scalar_rules=dict(stock_threshold=int(threshold[1]),stock_wrap=int(wrap[1]),district_fields=district_fields,
                          amount='float32(quantity) * float32(item price), rounded to float32, widened to float64 JDBC binding'),
        parameter_specialization=dict(warehouse=1,supplier_warehouse=1,all_local=1,source='existing adapter constants; not inferred from data'),
        unsupported='Any source revision; arbitrary reflection/SQL dialects/triggers/concurrency/failure timing; no general compiler claim')


def bind(query, parameters):
    parts=query.split('?'); assert len(parts)==len(parameters)+1
    return ''.join(part+(parameters[i] if i<len(parameters) else '') for i,part in enumerate(parts))


def emit(ir, mode, phase_events=None):
    assert mode in ('full','pruned','early_write','full_fault')
    # The backend still emits its reviewed source-order layout. Only a complete,
    # known region and whole-phase schedules preserving that layout are admitted.
    # Existing fault/mutant modes deliberately bypass this production-candidate
    # gate; they remain test controls, not accepted plans. The pruned variant's
    # separate FK obligation is still checked by the experiment schema gate.
    if mode in ('full', 'pruned'):
        graph = require_neworder_region(ir)
        selected = choose_placement(neworder_contract(graph))
        # The optimizer chooses the cuts. This existing backend can materialize
        # only its reviewed complete-region layout; other plans fail closed.
        if [r['nodes'] for r in selected['database_regions']] != [[n['id'] for n in graph['nodes'][1:-1]]]:
            raise ValueError('Selected placement has no implemented NewOrder backend')
        events = phase_events if phase_events is not None else schedule([n['id'] for n in graph['nodes']])
        checked = validate_schedule(graph, events)
        if not checked['accepted']:
            raise ValueError('Unsafe fusion phase schedule: '+str(checked['violations']))
    names=dict(w_id='p_w',d_id='p_d',c_id='p_c',o_id='v_order',d_next_o_id='v_order',
               o_ol_cnt='v_count',o_all_local='1',ol_i_id='v_items[v_i]',ol_supply_w_id='p_w',
               ol_quantity='v_quantities[v_i]',ol_number='v_i',ol_amount='v_amounts[v_i]',
               ol_dist_info='v_dist[v_i]',s_remote_cnt_increment='0',**{'s.s_quantity':'v_new_quantities[v_i]',
               'new Timestamp(System.currentTimeMillis())':'p_time'})
    generated=[]; mapping=[]
    def add(text, origin):
        start=len('\n'.join(generated).splitlines())+1
        generated.append(text)
        mapping.append(dict(start_line=start,end_line=start+len(text.splitlines())-1,origin=origin))
    def sql(field):
        spec=ir['sql'][field]
        args=[]
        for entry in spec['bindings']:
            expression=entry['expression']
            if expression not in names: raise ValueError('Unsupported JDBC expression: '+expression)
            args.append(names[expression])
        return bind(spec['sql'],args)
    def select(field,target):
        statement=sql(field); assert statement.startswith('SELECT ')
        return statement.replace(' FROM ',f' INTO {target} FROM ',1)+';'
    add(f'''CREATE OR REPLACE FUNCTION reversedb_fusion.neworder_{mode}(p_w integer,p_d integer,p_c integer,p_lines jsonb,p_time timestamp)
RETURNS jsonb LANGUAGE plpgsql VOLATILE SET search_path=public,pg_catalog AS $fusion$
DECLARE
  v_record record; v_stock record; v_item record; v_order integer; v_response_id integer;
  v_count integer := jsonb_array_length(p_lines); v_i integer; v_rows bigint;
  v_items integer[] := '{{}}'; v_quantities integer[] := '{{}}'; v_new_quantities integer[] := '{{}}';
  v_amounts double precision[] := '{{}}'; v_dist text[] := '{{}}'; v_price real;
  v_order_json jsonb; v_lines_json jsonb;
BEGIN
  FOR v_i IN 1..v_count LOOP
    v_items[v_i] := (p_lines->(v_i-1)->>'item')::integer;
    v_quantities[v_i] := (p_lines->(v_i-1)->>'quantity')::integer;
  END LOOP;''','backend ABI and representation rules; original AppRuntime validates inputs')
    pre=bind(ir['adapter_queries'][0],['p_d'])
    add(pre.replace(' FROM ',' INTO v_response_id FROM ',1)+";\nIF NOT FOUND THEN RETURN jsonb_build_object('__reversedb_error','missing_initial_district'); END IF;",'adapter initial order-id query; preserve outer JSONArray missing-row exception')
    rules={
        'getCustomer': "PERFORM "+sql('stmtGetCustSQL')[7:]+";\nIF NOT FOUND THEN RAISE EXCEPTION USING ERRCODE='RDB02',MESSAGE='C_D_ID='||p_d||' C_ID='||p_c||' not found!'; END IF;",
        'getWarehouse': "PERFORM "+sql('stmtGetWhseSQL')[7:]+";\nIF NOT FOUND THEN RAISE EXCEPTION USING ERRCODE='RDB02',MESSAGE='W_ID='||p_w||' not found!'; END IF;",
        'getDistrict': select('stmtGetDistSQL','v_record')+"\nIF NOT FOUND THEN RAISE EXCEPTION USING ERRCODE='RDB02',MESSAGE='D_ID='||p_d||' D_W_ID='||p_w||' not found!'; END IF;\nv_order := v_record.d_next_o_id;",
        'updateDistrict': sql('stmtUpdateDistSQL')+";\nGET DIAGNOSTICS v_rows = ROW_COUNT;\nIF v_rows=0 THEN RAISE EXCEPTION USING ERRCODE='RDB02',MESSAGE='Error!! Cannot update next_order_id on district for D_ID='||p_d||' D_W_ID='||p_w; END IF;",
        'insertOpenOrder':sql('stmtInsertOOrderSQL')+';',
        'insertNewOrder':sql('stmtInsertNewOrderSQL')+';'}
    for helper in ir['helper_order'][:6]:
        if mode=='pruned' and helper=='getWarehouse': continue
        add(rules[helper],dict(method=helper,rule='inline helper; preserve successful-path SQL order and business checks'))
    add('FOR v_i IN 1..v_count LOOP','newOrderTransaction ordered staging loop')
    add(select('stmtGetItemSQL','v_item')+"\nIF NOT FOUND THEN RAISE EXCEPTION USING ERRCODE='RDB01',MESSAGE='EXPECTED new order rollback: I_ID='||v_items[v_i]||' not found!'; END IF;\nv_price := v_item.i_price::real;\nv_amounts[v_i] := (v_quantities[v_i]::real * v_price)::real::double precision;",'getItemPrice + Java float32 arithmetic lowering')
    add(select('stmtGetStockSQL','v_stock')+"\nIF NOT FOUND THEN RAISE EXCEPTION USING ERRCODE='RDB02',MESSAGE='S_I_ID='||v_items[v_i]||' not found!'; END IF;",'getStock: retain FOR UPDATE and missing-stock failure')
    rule=ir['scalar_rules']
    add(f"v_new_quantities[v_i] := CASE WHEN v_stock.s_quantity-v_quantities[v_i]>={rule['stock_threshold']} THEN v_stock.s_quantity-v_quantities[v_i] ELSE v_stock.s_quantity-v_quantities[v_i]+{rule['stock_wrap']} END;",'getStock source arithmetic')
    add('v_dist[v_i] := CASE p_d '+' '.join(f'WHEN {n} THEN v_stock.{field}' for n,field in rule['district_fields'])+' ELSE NULL END;','getDistInfo complete 10-arm source switch')
    if mode=='early_write': add(sql('stmtUpdateStockSQL')+';','DELIBERATELY WRONG: apply stock write before subsequent staged reads')
    add('END LOOP;','end staging; no database stock mutation in correct variants')
    for variable in ir['batch_order']:
        if variable=='stmtUpdateStock' and mode=='early_write': continue
        field='stmtInsertOrderLineSQL' if variable=='stmtInsertOrderLine' else 'stmtUpdateStockSQL'
        add('FOR v_i IN 1..v_count LOOP\n'+sql(field)+';\nEND LOOP;',dict(source_batch=variable,rule='ordered batch lowering; no user triggers, normal SQL completion, serial profile'))
    order=bind(ir['adapter_queries'][1],['p_d','v_response_id'])
    lines=bind(ir['adapter_queries'][2],['p_d','v_response_id'])
    if mode=='full_fault':
        add("RAISE EXCEPTION USING ERRCODE='58000',MESSAGE='injected failure after writes';",'TEST ONLY: late failure before result projection')
    add(f"SELECT to_jsonb(r) INTO v_order_json FROM ({order}) r;\nSELECT coalesce(jsonb_agg(to_jsonb(r) ORDER BY r.ol_number),'[]'::jsonb) INTO v_lines_json FROM ({lines}) r;\nRETURN jsonb_build_object('order',v_order_json,'lines',v_lines_json);\nEND;\n$fusion$;",'adapter complete SELECT * results; timestamp serialization normalized at outer ABI')
    return '\n'.join(generated)+'\n',mapping


HELPER = '''package com.oltpbenchmark.api;
import java.sql.*;
import org.json.*;
import org.postgresql.jdbc.PgConnection;
import org.postgresql.util.PSQLException;
import reversedb.tpcc.AppRuntime;

/** Generated outer ABI; one JDBC request remains, internal per-step JDBC is gone. */
public final class FusedNewOrder {
  public static JSONObject execute(Connection connection, JSONObject command) throws Exception {
    String mode=System.getProperty("reversedb.fusion.mode","full");
    if (!java.util.Set.of("full","pruned","early_write","full_fault").contains(mode)) throw new IllegalArgumentException("Unknown fusion mode");
    try (PreparedStatement statement=connection.prepareStatement("SELECT reversedb_fusion.neworder_"+mode+"(?,?,?,?::jsonb,?::timestamp)")) {
      statement.setInt(1,1); statement.setInt(2,command.getInt("district")); statement.setInt(3,command.getInt("customer"));
      statement.setString(4,command.getJSONArray("lines").toString());
      // Keep the original driver timestamp codec, including infinity sentinels.
      statement.setTimestamp(5,new Timestamp(command.getLong("clock_ms")));
      try (ResultSet rows=statement.executeQuery()) {
        if (!rows.next()) throw new IllegalStateException("Missing fused result");
        JSONObject result=new JSONObject(rows.getString(1));
        if ("missing_initial_district".equals(result.optString("__reversedb_error"))) throw new JSONException("JSONArray[0] not found.");
        var timestamps=connection.unwrap(PgConnection.class).getTimestampUtils();
        JSONObject order=result.getJSONObject("order");
        if (!order.isNull("o_entry_d")) order.put("o_entry_d",timestamps.toTimestamp(null,order.getString("o_entry_d").replace('T',' ')).toString());
        for (Object value:result.getJSONArray("lines")) {
          JSONObject line=(JSONObject)value;
          if (!line.isNull("ol_delivery_d")) line.put("ol_delivery_d",timestamps.toTimestamp(null,line.getString("ol_delivery_d").replace('T',' ')).toString());
        }
        return result;
      }
    } catch (PSQLException error) {
      if ("RDB01".equals(error.getSQLState())) throw new AppRuntime.BusinessAbort();
      if ("RDB02".equals(error.getSQLState())) throw new RuntimeException(error.getServerErrorMessage().getMessage());
      throw error;
    }
  }
}
'''


def adapter_variant(adapter):
    start=adapter.index('    if (type.equals("NewOrder")) {')
    # Replace only this frozen source block. Other generated dispatches may
    # already sit between it and the original procedure switch.
    end=adapter.index('\n    }\n',start)+len('\n    }\n')
    return adapter[:start]+'    if (type.equals("NewOrder")) return FusedNewOrder.execute(c,cmd);\n'+adapter[end:]
