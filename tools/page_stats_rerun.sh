#!/bin/bash
#SBATCH -J page-stats
#SBATCH -p cpu-ms,cpu-troja
#SBATCH -o page-stats.%j.%N.out
#SBATCH -e page-stats.%j.%N.err
#SBATCH --time 1-00:00:00
#SBATCH --mem=50G
#SBATCH --cpus-per-task=12

# tools/page_stats_rerun.sh -- the page statistics of the 2026-10-12 corpus run, aggregated again with the
# category ratios of issue #8, and checked against that run.
#
# For each collection (default: ARUP ARUB) it reads the classified line tables the 10-12 run left,
# $DATA/<COLL>/DOC_LINE_CATEG_1210/, and writes next to them, never over them:
#   $DATA/<COLL>/page_stats_1210r.csv           the page table, now with clear_rat ... empty_rat
#   $DATA/<COLL>/DOC_LINE_STATS_1210r/          one stats_<doc>.csv per document
#   $DATA/<COLL>/page_stats_1210r.config.txt    the configuration it ran with
#   $DATA/<COLL>/page_stats_1210r_report.md     tools/page_stats_check.py's report against page_stats_1210.csv
# No document record is written ([DOCUMENT] JSON_DIR empty, DOCUMENT_JSON_DIR unset), as in the 10-12 run.
# The paradata goes to paradata/ in the checkout, as for every stage; the report names the file.
#
# Submit it from the root of any checkout that carries #8:
#   cd /lnet/work/projects/atrium/GITHUB/atrium-ocr-postprocess && sbatch tools/page_stats_rerun.sh [COLL ...]
# Settings, from the environment sbatch passes on:
#   DATA  the folder that holds the collections (default /lnet/work/projects/atrium)
#   REPO  the checkout (default: the folder sbatch was run from)
#   VENV  the virtual environment (default: the one active at submission, else venv-ocr, venv-lang or venv
#         in the checkout)
#   IN    the run to repeat (default 1210); OUT the rerun (default ${IN}r)
# A collection that already holds the rerun's output is refused, not overwritten. Exit 0 when every report
# passes, 1 when one fails, 2 when a collection could not be run.

set -euo pipefail
hostname
date

DATA=${DATA:-/lnet/work/projects/atrium}
REPO=${REPO:-${SLURM_SUBMIT_DIR:-$PWD}}
IN=${IN:-1210}
OUT=${OUT:-${IN}r}
COLLS=("$@")
[ ${#COLLS[@]} -gt 0 ] || COLLS=(ARUP ARUB)

if [ ! -f "$REPO/aggregate_STAT.py" ] || [ ! -f "$REPO/tools/page_stats_check.py" ]; then
    echo "$REPO is not a checkout carrying #8 (no aggregate_STAT.py or tools/page_stats_check.py);" \
        "submit from the checkout's root or set REPO" >&2
    exit 2
fi
if [ -z "${VENV:-}" ] && [ -n "${VIRTUAL_ENV:-}" ]; then
    VENV=$VIRTUAL_ENV
fi
if [ -z "${VENV:-}" ]; then
    for candidate in "$REPO/venv-ocr" "$REPO/venv-lang" "$REPO/venv"; do
        if [ -f "$candidate/bin/activate" ]; then
            VENV=$candidate
            break
        fi
    done
fi
if [ -z "${VENV:-}" ] || [ ! -f "$VENV/bin/activate" ]; then
    echo "no virtual environment found (VENV=${VENV:-unset}); set VENV to one with pandas and tqdm" >&2
    exit 2
fi

set +u # an activate script may read unset variables
# shellcheck disable=SC1091
source "$VENV/bin/activate"
set -u
cd "$REPO"
unset DOCUMENT_JSON_DIR
REVISION=$(git describe --always --dirty 2>/dev/null || echo unknown)
echo "checkout $REPO at $REVISION; $(python3 --version) from $VENV; collections in $DATA"

status=0
for COLL in "${COLLS[@]}"; do
    DIR=$DATA/$COLL
    echo "== $COLL: $DIR/DOC_LINE_CATEG_$IN -> page_stats_$OUT.csv, DOC_LINE_STATS_$OUT/"
    if [ ! -d "$DIR/DOC_LINE_CATEG_$IN" ]; then
        echo "   $DIR/DOC_LINE_CATEG_$IN: not found; $COLL not run" >&2
        status=2
        continue
    fi
    if [ -e "$DIR/page_stats_$OUT.csv" ] || [ -n "$(ls -A "$DIR/DOC_LINE_STATS_$OUT" 2>/dev/null)" ]; then
        echo "   $DIR already holds page_stats_$OUT.csv or DOC_LINE_STATS_$OUT/; remove them to run $COLL again" >&2
        status=2
        continue
    fi

    CFG=$DIR/page_stats_$OUT.config.txt
    cat >"$CFG" <<EOF
# Written by tools/page_stats_rerun.sh on $(date -Iseconds), job ${SLURM_JOB_ID:-none}, checkout $REVISION.
[AGGREGATE]
RAW_LINES_CSV = $DIR/DOC_LINE_CATEG_$IN
OUTPUT_DOC_DIR = $DIR/DOC_LINE_STATS_$OUT
OUTPUT_STATS = $DIR/page_stats_$OUT.csv
STANDARD_COLS = Clear,Noisy,Trash,Non-text,Empty

[DOCUMENT]
JSON_DIR =
EOF

    MARK=$(mktemp)
    if ! python3 aggregate_STAT.py --config "$CFG"; then
        echo "   aggregate_STAT.py failed on $COLL" >&2
        rm -f "$MARK"
        status=2
        continue
    fi
    PARADATA=$(find paradata -maxdepth 1 -name '*_ocr-postprocess.json' -newer "$MARK" | sort | tail -n 1)
    rm -f "$MARK"

    CHECK=(python3 tools/page_stats_check.py "$DIR" --old "$IN" --new "$OUT" --config "$CFG")
    [ -z "$PARADATA" ] || CHECK+=(--paradata "$PARADATA")
    "${CHECK[@]}" || { rc=$?; [ "$status" -ge "$rc" ] || status=$rc; }
done

date
exit "$status"
