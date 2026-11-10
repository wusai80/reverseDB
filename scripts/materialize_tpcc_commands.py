#!/usr/bin/env python3
"""Derive explicit commands from one frozen, version-specific JDBC trace (not a general importer)."""
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'evidence/tpcc-replay/20261103-7e3eaafec61d/replay_a/transactions.json'


def materialize(events):
    commands = []
    for event in events:
        original = event['input']
        command = {k: original[k] for k in ['id', 'type', 'terminal', 'clock_ms']}
        command['warehouse'] = 1
        bindings, executions = {}, []
        for op in event['trace']:
            sql = op.get('sql', '').lower()
            args = op.get('args', [])
            if op['op'].startswith('set') and args and type(args[0]) is int:
                bindings.setdefault(sql, {})[args[0]] = args[1]
            if op['op'] in ['executeQuery', 'executeUpdate', 'addBatch']:
                executions.append((sql, dict(bindings.get(sql, {}))))
        def selected(text):
            return [values for sql, values in executions if text in sql]
        kind = original['type']
        if kind == 'NewOrder':
            order = selected('insert into oorder')[0]
            assert order[3] == 1 and order[7] == 1
            command.update(district=order[2], customer=order[4])
            if 'items' in original:
                # Positive nonexistent ID is a business abort; the new input contract rejects negative IDs.
                command['lines'] = [dict(item=item if item > 0 else 100001, quantity=q)
                                    for item, q in zip(original['items'], original['quantities'])]
            else:
                lines = selected('insert into order_line')
                assert len(lines) == order[6] and all(line[6] == 1 for line in lines)
                command['lines'] = [dict(item=line[5], quantity=line[7]) for line in lines]
        elif kind == 'Payment':
            h = selected('insert into history')[0]
            assert h[2] == h[5] == 1
            command.update(district=h[4], customer_district=h[1], customer=h[3], amount=round(h[7], 2))
        elif kind == 'OrderStatus':
            lookup = selected('order by o_id desc')[0]
            command.update(district=lookup[2], customer=lookup[3])
        elif kind == 'StockLevel':
            lookup = selected('s_quantity <')[0]
            command.update(district=lookup[2], threshold=lookup[6])
        elif kind == 'Delivery':
            command['carrier'] = selected('set o_carrier_id')[0][1]
        else:
            raise ValueError(kind)
        commands.append(command)
    return {'version': 1, 'commands': commands,
            'provenance': {'source': str(SOURCE.relative_to(ROOT)),
                           'sha256': hashlib.sha256(SOURCE.read_bytes()).hexdigest(),
                           'transformations': ['Resolve selected customer to ID', 'Round float payment to original two-decimal input',
                                               'Replace invalid sentinel -12345 with nonexistent positive item 100001']}}


if __name__ == '__main__':
    path = ROOT / 'app/tpcc/scenarios/baseline.json'
    path.write_text(json.dumps(materialize(json.loads(SOURCE.read_text())), indent=2) + '\n')
    print(path)
