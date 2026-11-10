"""Untimed independent-process query/output order check for the original export."""
import hashlib
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[2]
SOURCE=ROOT/'work/beets/source-e978874d'
sys.path.insert(0,str(SOURCE))
evidence=Path(sys.argv[1]);out=Path(sys.argv[2]);out.mkdir(parents=True,exist_ok=True)
os.environ['BEETSDIR']=str(evidence/'config')
from beets import config
from beets.dbcore.db import Transaction
from beets.library import Library
from beetsplug.export import ExportPlugin

config['plugins']=[]
lib=Library(evidence/'100/library.db',directory=evidence/'100/music')
queries=[];original=Transaction.query
def observe(tx,sql,params=()):
    queries.append(dict(sql=sql,params=list(params)))
    return original(tx,sql,params)
plugin=ExportPlugin();target=out/'output.json'
opts=SimpleNamespace(output=str(target),append=False,format='json',library=True,album=False,included_keys=[],limit=None)
with patch.object(Transaction,'query',observe):plugin.run(lib,opts,['id+'])
lib._close()
expected=json.loads((evidence/'100/queries.json').read_text())
assert queries==expected
assert hashlib.sha256(target.read_bytes()).hexdigest()==hashlib.sha256((evidence/'100/plain-1.json').read_bytes()).hexdigest()
(out/'result.json').write_text(json.dumps(dict(status='passed',hash_seed=os.environ.get('PYTHONHASHSEED'),
    query_count=len(queries),queries_match_original=True,output_sha256=hashlib.sha256(target.read_bytes()).hexdigest(),timing=False),indent=2)+'\n')
print('Original query sequence and complete output match under PYTHONHASHSEED='+os.environ['PYTHONHASHSEED'])
