"""Choose a reviewed plan from a bound portfolio; does not deploy it."""
import argparse
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from prototype.adaptive_planning.selector import select

if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('catalog',type=Path);ap.add_argument('--out',type=Path)
    args=ap.parse_args();result=select(json.loads(args.catalog.read_text()),ROOT)
    text=json.dumps(result,indent=2)+'\n'
    if args.out:args.out.write_text(text)
    else:print(text,end='')
