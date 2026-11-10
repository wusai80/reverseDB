ReverseDB — ISSTA 2027 anonymous review manuscript

Files
  main.tex         Complete manuscript, including figures and tables.
  references.bib   Bibliography.
  results-tables.tex  Generated tables used by main.tex; required for compilation.
  README.txt       Build and submission-format notes.

Overleaf
  Upload the ZIP as a new project and select main.tex as the main document.
  Use pdfLaTeX with an ACM acmart-compatible TeX installation.
  The validated local build uses TeX Live 2026 and acmart v2.20.
  No private macros, external figures, or project-specific class files are needed.

Command-line build
  pdflatex -interaction=nonstopmode -halt-on-error main.tex
  bibtex main
  pdflatex -interaction=nonstopmode -halt-on-error main.tex
  pdflatex -interaction=nonstopmode -halt-on-error main.tex

Review format
  Class: acmart, with acmsmall, screen, review, anonymous.
  The document retains default ACM typography and single-column geometry,
  line numbers, page numbers, CCS concepts, and ACM reference formatting.
  Conference-style metadata follows the sample-acmsmall-conf structure.
  The book title explicitly identifies the manuscript as a submission.
  DOI, ISBN, and rights metadata have not been assigned; none are fabricated.
  The full title and author list must be finalized before actual submission.

Research disclosure
  The Methods subsection "Use of AI in the Research Process" describes AI assistance in study design, implementation,
  execution, analysis, and verification, including limitations of the checks.
  Data Availability appears immediately before References and explains the
  current absence of an anonymous portable experimental artifact.
  This ZIP contains manuscript sources, not the experimental replication package.

Current evaluation (2026-11-10, five-subject revision)
  Fresh fixed-protocol runs cover Java/PostgreSQL, alf.io, Oscar, beets, Fossil.
  Admission and performance are separate: the common framework selects Java
  fusion and Fossil deletion, but retains original for alf.io, Oscar and beets.
  Oscar and beets experimental timing gains do not close missing obligations.
  alf.io is an identical-code A/A control, not an optimization speedup.
  A separate Fossil module factorial enables SQL-side and C-side removal alone
  and together. All 508 module-study executions preserve complete data.
  The older batches remain archived; the fresh Oscar gain does not overwrite
  its earlier no-stable-gain result. No samples are pooled across batches.
  The framework's policy was frozen before fresh development-subject repeats;
  the experiment is not held-out project transfer or independent test costs.
  scripts/update_five_project_results.py generates results-tables.tex and
  results.json from the fresh evidence. These scripts/data are not needed to
  build this manuscript ZIP and are not included in it.

Official requirements
  https://conf.researchr.org/track/issta-2027/issta-2027-research-papers
  Maximum 18 pages excluding Data Availability and references; appendices count.
  This source package is prepared for review; it has not been submitted.
