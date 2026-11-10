#!/usr/bin/env python3
"""Five bounded responsibilities; test only the FK-implied warehouse check."""
import json
from pathlib import Path
from app_db_smoke import ROOT, ARCHIVE, digest
from test_name_lookup_pruning import main


def screen():
    source_dir = ARCHIVE / 'bundle/sources/reference/com/oltpbenchmark/benchmarks/tpcc/procedures'
    manifest = json.loads((ARCHIVE / 'manifest.json').read_text())
    sources = {name: source_dir / (name + '.java') for name in ('NewOrder', 'Payment', 'Delivery', 'StockLevel')}
    observation = ROOT / 'evidence/tpcc-observation/20261103-8729d2283fdc'
    sources['Adapter'] = observation / 'run/inputs/app/tpcc/src/com/oltpbenchmark/api/TpccApp.java'
    for name, path in sources.items():
        expected = (json.loads((observation / 'manifest.json').read_text())[str(path.relative_to(observation))]
                    if name == 'Adapter' else manifest[str(path.relative_to(ARCHIVE))])
        assert digest(path) == expected

    def witness(name, text):
        source = sources[name].read_text()
        assert source.count(text) == 1, (name, text)
        return dict(file=str(sources[name]), sha256=digest(sources[name]),
                    line=source[:source.index(text)].count('\n') + 1, text=text)

    candidates = [
        dict(id='N1', transaction='NewOrder', module='getCustomer', decision='retain',
             reason='Void existence check gates all subsequent writes; no earlier retained proof of this customer exists.',
             query_prefixes=['SELECT C_DISCOUNT'],
             witnesses=[witness('NewOrder','this.getCustomer(conn, w_id, d_id, c_id);'),
                        witness('NewOrder','throw new RuntimeException("C_D_ID=" + d_id + " C_ID=" + c_id + " not found!");')]),
        dict(id='N2', transaction='NewOrder', module='getWarehouse', decision='candidate_for_guarded_experiment',
             reason='Successful preceding customer check plus enforced immediate customer→district→warehouse FKs implies warehouse existence in the same visible snapshot. Query faults/read effects remain outside the contract.',
             query_prefixes=['SELECT W_TAX'],
             witnesses=[witness('NewOrder','this.getCustomer(conn, w_id, d_id, c_id);\n      this.getWarehouse(conn, w_id);'),
                        witness('NewOrder','throw new RuntimeException("W_ID=" + w_id + " not found!");')]),
        dict(id='P1', transaction='Payment', module='getWarehouse + getDistrict history metadata', decision='retain',
             reason='Warehouse/district names flow to history.h_data, a retained database field, even though not included in the payment response.',
             query_prefixes=['SELECT W_STREET_1','SELECT D_STREET_1'],
             witnesses=[witness('Adapter','invoke(p,"insertHistory",c,1,d,cd,1,amount,warehouse.w_name,district.d_name,cust);'),
                        witness('Payment','String h_data = w_name + "    " + d_name;'),
                        witness('Payment','payInsertHist.setString(8, h_data);')]),
        dict(id='D1', transaction='Delivery', module='getOrderLineTotal', decision='retain',
             reason='The aggregate is passed to updateBalanceAndDelivery and changes customer balance.',
             query_prefixes=['SELECT SUM(OL_AMOUNT)'],
             witnesses=[witness('Adapter','float total=(Float)invoke(p,"getOrderLineTotal",c,1,district,order);'),
                        witness('Adapter','invoke(p,"updateBalanceAndDelivery",c,1,district,cid,total);'),
                        witness('Delivery','delivUpdateCustBalDelivCnt.setBigDecimal(1, BigDecimal.valueOf(orderLineTotal));')]),
        dict(id='S1', transaction='StockLevel', module='getOrderId query window', decision='retain',
             reason='The returned order boundary sets both ends of the 20-order stock-count window; it affects the public count.',
             query_prefixes=['SELECT D_NEXT_O_ID'],
             witnesses=[witness('Adapter','int next=(Integer)invoke(p,"getOrderId",c,1,d);'),
                        witness('StockLevel','stockGetCountStock.setInt(3, o_id);\n         stockGetCountStock.setInt(4, o_id - 20);')]),
    ]
    traces = ROOT / 'evidence/module-name-pruning/20261105-b14245195231'
    trace_manifest = json.loads((traces / 'manifest.json').read_text())
    evidence_files = {}
    for candidate in candidates:
        candidate['observed_queries_by_scenario'] = {}
    for scenario in ('baseline', 'holdout', 'transaction_edges', 'delivery_edges'):
        commands_path = ARCHIVE / f'run/inputs/app/tpcc/scenarios/{scenario}.json'
        assert digest(commands_path) == manifest[str(commands_path.relative_to(ARCHIVE))]
        commands = {item['id']: item for item in json.loads(commands_path.read_text())['commands']}
        trace_path = traces / (scenario + '_original/jdbc-trace.json')
        assert digest(trace_path) == trace_manifest[str(trace_path.relative_to(traces))]
        evidence_files[str(trace_path)] = digest(trace_path)
        rows = json.loads(trace_path.read_text())
        for candidate in candidates:
            count = sum(event['op'] == 'executeQuery' and any(event.get('sql','').startswith(prefix) for prefix in candidate['query_prefixes'])
                        for row in rows if commands[row['id']]['type'] == candidate['transaction'] for event in row['events'])
            candidate['observed_queries_by_scenario'][scenario] = count
    assert all(sum(candidate['observed_queries_by_scenario'].values()) > 0 for candidate in candidates)
    return dict(scope='Five executed responsibilities across four transactions; no new workload or domain restriction',
                classification='Manual source-bound reasoning, with reused verified runtime traces; not learned labels or automatic proof',
                candidates=candidates, candidates_for_experiment=1, retained=4, source_files={str(p):digest(p) for p in sources.values()},
                trace_files=evidence_files)


if __name__ == '__main__':
    main(warehouse=True)
