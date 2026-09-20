#!/usr/bin/env bash
cd "C:/Users/Administrator/WorkBuddy/2026-09-20-07-51-17/photocrawl"
PY="C:/Users/Administrator/.workbuddy/binaries/python/envs/default/Scripts/python.exe"
run(){
  name="$1"; cap="$2"; shift 2
  echo "############ $name  cap=$cap  $* ############"
  "$PY" crawl.py run --rule "rules/$name.json" --out "beauty1000/$name" \
      --max-total "$cap" --workers 10 --delay 0.1 --retries 2 --timeout 20 "$@" \
      > "tools/logs/$name.log" 2>&1
  echo "$name EXIT=$?"
  tail -4 "tools/logs/$name.log"
}
run 4kup     250 --proxy local
run taotu    220 --proxy local
run misskon  120 --proxy local
run v2ph      60 --proxy local
run yituyu    50
echo "ALL DONE"
