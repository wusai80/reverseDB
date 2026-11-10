#!/usr/bin/env python3
"""Bind one conservative proposal to archived observations; no automatic admission."""
import json
import hashlib
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
BASE=ROOT/'evidence/alfio-dependencies/20261106-f8e5aaa63540'


def main():
    out=BASE.with_name(BASE.name+'-candidate'); out.mkdir(exist_ok=False)
    manifest=json.loads((BASE/'manifest.json').read_text())
    path=BASE/'dependencies.json'
    assert hashlib.sha256(path.read_bytes()).hexdigest()==manifest['dependencies.json']
    data=json.loads(path.read_text()); witnesses=[]
    def method(row,name): return any(x.startswith('alfio/repository/TicketRepository#'+name+'(') for x in row['annotation_matches'])
    for operation,count in [('01-reserve-three',3),('04-reserve-two',2),('10-reserve-one',1)]:
        rows=[r for r in data['observed_statements'] if r['operation']==operation]
        writes=[r for r in rows if method(r,'batchReserveTickets')]
        reads=[r for r in rows if method(r,'findById')]
        assert len(writes)==count and len(reads)==1
        between=rows[rows.index(writes[-1])+1:rows.index(reads[0])]
        assert len(between)==2*count
        assert all(any('#findAdditionalFieldsForEvent(' in x or '#findAllForContextByTicketId(' in x for x in row['annotation_matches']) for row in between)
        witnesses.append(dict(operation=operation,batch_statements=[w['id'] for w in writes],first_ticket_read=reads[0]['id'],intervening_statements=[r['id'] for r in between],intervening_count=len(between)))
    failed=[r for r in data['observed_statements'] if r['operation']=='03-insufficient-stock']
    assert not any(method(r,'batchReserveTickets') for r in failed)
    assert failed[-1]['sql'].upper()=='ROLLBACK'
    proposal=ROOT/'prototype/alfio/ordered_batch_candidate.json'
    (out/'candidate.json').write_bytes(proposal.read_bytes())
    (out/'assess_alfio_candidate.py').write_bytes(Path(__file__).read_bytes())
    (out/'support.json').write_text(json.dumps(dict(status='proposal_bound_to_evidence_not_admitted',dependency_evidence_sha256=manifest['dependencies.json'],witnesses=witnesses,
      insufficient_stock='no batch-write region reached; original lock/read then rollback retained',
      check_scope='observed ordering and template matching only; no alias, effect-equivalence or performance proof'),indent=2)+'\n')
    hashes={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in out.iterdir() if p.is_file()}
    (out/'manifest.json').write_text(json.dumps(hashes,indent=2)+'\n')
    print(json.dumps(dict(output=str(out),witnesses=len(witnesses),status='proposed_not_admitted')))


if __name__=='__main__': main()
