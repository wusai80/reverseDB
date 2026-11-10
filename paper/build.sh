#!/bin/bash
set -eu
cd "$(dirname "$0")"
REVERSEDB_TEX_BIN="${REVERSEDB_TEX_BIN:-/Users/wusai/.TinyTeX/bin/universal-darwin}"
mkdir -p build ../output/pdf
"$REVERSEDB_TEX_BIN/pdflatex" -interaction=nonstopmode -halt-on-error -output-directory=build main.tex > build/latex-first.stdout 2>&1
(
  cd build
  BIBINPUTS=..: "$REVERSEDB_TEX_BIN/bibtex" main > bibtex.stdout 2>&1
)
"$REVERSEDB_TEX_BIN/pdflatex" -interaction=nonstopmode -halt-on-error -output-directory=build main.tex > build/latex-second.stdout 2>&1
"$REVERSEDB_TEX_BIN/pdflatex" -interaction=nonstopmode -halt-on-error -output-directory=build main.tex > build/latex-final.stdout 2>&1
cp build/main.pdf ../output/pdf/reversedb-issta-draft.pdf
python3 - <<'PY'
from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED
with ZipFile('reversedb-issta-source.zip', 'w', ZIP_DEFLATED) as archive:
    for name in ('main.tex', 'results-tables.tex', 'references.bib', 'README.txt'):
        archive.write(Path(name), name)
PY
