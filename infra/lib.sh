# Shared helpers for the Docker-only test scripts (test-docker.sh, test-e2e.sh, test-inventory.sh).
# Sourced, not executed: no shebang, no `set` (the caller's own `set -eu` applies).
# Every ni_* function assumes POSIX sh; no bashisms.

# ni_project <prefix> -> prints a unique Compose project name.
ni_project() {
  echo "newsintel-$1-$$-$(date +%s)"
}

# ni_single_head <compose> <service> <message>
# Runs the duplicated "exactly one migration head" assertion; exits 1 with <message> on stderr
# if it fails (matches the two scripts' pre-existing, slightly different wording).
ni_single_head() {
  ni_sh_compose=$1
  ni_sh_service=$2
  ni_sh_message=$3
  [ "$($ni_sh_compose run --rm "$ni_sh_service" alembic heads | grep -c '(head)')" = 1 ] || {
    echo "$ni_sh_message" >&2
    exit 1
  }
}

# ni_report_init <override> <root> <label>
# Sets the global $artifacts (from <override> if non-empty, else
# <root>/docs/archive/testing/<date>-<label>-<n>, picking the first unused <n>), creates it, and
# writes the timings.tsv header. Must run before any ni_stage/ni_env_report/ni_mem_start call.
ni_report_init() {
  if [ -n "$1" ]; then
    artifacts=$1
  else
    ni_ri_date=$(date +%Y-%m-%d)
    ni_ri_n=1
    while [ -d "$2/docs/archive/testing/$ni_ri_date-$3-$ni_ri_n" ]; do
      ni_ri_n=$((ni_ri_n + 1))
    done
    artifacts="$2/docs/archive/testing/$ni_ri_date-$3-$ni_ri_n"
  fi
  mkdir -p "$artifacts"
  printf 'stage\tstart_epoch\telapsed_seconds\tstatus\n' > "$artifacts/timings.tsv"
  : > "$artifacts/memory.tsv"
  ni_stage_name=
  ni_stage_start=
  ni_finalized=0
  ni_mem_pid=
}

# ni_stage <name>
# Closes the previous stage (recorded at status 0 -- reaching this call under `set -eu` proves it
# succeeded), opens <name>, and prints "==> <name>".
ni_stage() {
  ni_st_now=$(date +%s)
  if [ -n "${ni_stage_name:-}" ]; then
    printf '%s\t%s\t%s\t%s\n' "$ni_stage_name" "$ni_stage_start" "$((ni_st_now - ni_stage_start))" 0 \
      >> "$artifacts/timings.tsv"
  fi
  ni_stage_name=$1
  ni_stage_start=$ni_st_now
  echo "==> $1"
}

# ni_env_report <root> <cache_state>
# Writes environment.txt: git revision/dirty state, docker/compose versions, nproc, MemTotal, and
# the caller-supplied cache-state flag (e.g. "initial" or "warm").
ni_env_report() {
  {
    echo "git_rev=$(git -C "$1" rev-parse HEAD 2>/dev/null || echo unknown)"
    if git -C "$1" status --porcelain 2>/dev/null | grep -q .; then
      echo "git_dirty=true"
    else
      echo "git_dirty=false"
    fi
    echo "docker_version=$(docker --version 2>/dev/null || echo unknown)"
    echo "compose_version=$(docker compose version 2>/dev/null || echo unknown)"
    echo "nproc=$(nproc 2>/dev/null || echo unknown)"
    echo "mem_total_kb=$(awk '/^MemTotal:/{print $2}' /proc/meminfo 2>/dev/null || echo unknown)"
    echo "cache_state=$2"
  } > "$artifacts/environment.txt"
}

# ni_mem_start <project>
# Backgrounds a docker-stats sampler polling every $NEWSINTEL_MEM_SAMPLE_SECONDS (default 10)
# against containers labeled with the given Compose project. Writes raw samples to memory.tsv
# (timestamp, container, raw MemUsage-used token); ni_report_finalize normalizes and summarizes.
ni_mem_start() {
  ni_mem_project=$1
  (
    while :; do
      ni_mem_ids=$(docker ps -q --filter "label=com.docker.compose.project=$ni_mem_project" 2>/dev/null) || ni_mem_ids=
      if [ -n "$ni_mem_ids" ]; then
        ni_mem_ts=$(date +%s)
        # Word-splitting $ni_mem_ids into separate docker-stats arguments is intentional.
        docker stats --no-stream --format '{{.Name}}|{{.MemUsage}}' $ni_mem_ids 2>/dev/null |
          while IFS='|' read -r ni_mem_name ni_mem_usage; do
            printf '%s\t%s\t%s\n' "$ni_mem_ts" "$ni_mem_name" "${ni_mem_usage%% / *}" >> "$artifacts/memory.tsv"
          done
      fi
      sleep "${NEWSINTEL_MEM_SAMPLE_SECONDS:-10}"
    done
  ) &
  ni_mem_pid=$!
}

# ni_mem_stop: stops the background sampler started by ni_mem_start, if any.
ni_mem_stop() {
  if [ -n "${ni_mem_pid:-}" ]; then
    kill "$ni_mem_pid" >/dev/null 2>&1 || true
    wait "$ni_mem_pid" 2>/dev/null || true
    ni_mem_pid=
  fi
  return 0
}

# ni_report_finalize <status>
# First statement of each script's cleanup(): closes any still-open stage at <status>, then writes
# timings.txt (slowest-first + TOTAL) and memory.txt (per-container + aggregate sampled peaks, in
# bytes, with documented limitations). Defensive: never lets a reporting failure abort cleanup(),
# since a caller's `compose down` still has to run after this returns.
ni_report_finalize() {
  if [ "${ni_finalized:-0}" = 1 ]; then
    return 0
  fi
  ni_finalized=1
  if [ -z "${artifacts:-}" ] || [ ! -f "$artifacts/timings.tsv" ]; then
    return 0
  fi

  ni_mem_stop

  if [ -n "${ni_stage_name:-}" ]; then
    ni_rf_now=$(date +%s)
    printf '%s\t%s\t%s\t%s\n' "$ni_stage_name" "$ni_stage_start" "$((ni_rf_now - ni_stage_start))" "$1" \
      >> "$artifacts/timings.tsv"
    ni_stage_name=
  fi

  {
    echo "Stage timings (slowest first):"
    tail -n +2 "$artifacts/timings.tsv" | sort -t "$(printf '\t')" -k3,3nr |
      awk -F'\t' '{printf "  %-28s %6ss  status=%s\n", $1, $3, $4}'
    ni_rf_total=$(tail -n +2 "$artifacts/timings.tsv" | awk -F'\t' '{s += $3} END {print s + 0}')
    printf "%-30s %6ss\n" TOTAL "$ni_rf_total"
  } > "$artifacts/timings.txt" 2>/dev/null || true

  {
    echo "Memory report (docker stats --no-stream, sampled every ${NEWSINTEL_MEM_SAMPLE_SECONDS:-10}s)"
    echo "Limitations: the sampling interval can miss short spikes between polls; values are"
    echo "per-container cgroup memory usage, not host or BuildKit peak memory (buildkitd runs"
    echo "outside the Compose project label, so build memory is invisible to this sampler)."
    echo ""
    if [ -s "$artifacts/memory.tsv" ]; then
      awk -F'\t' '
        function to_bytes(v,   n, u) {
          if (!match(v, /^[0-9.]+/)) return 0
          n = substr(v, RSTART, RLENGTH)
          u = substr(v, RSTART + RLENGTH)
          if (u == "GiB") return n * 1024 * 1024 * 1024
          if (u == "MiB") return n * 1024 * 1024
          if (u == "KiB") return n * 1024
          return n + 0
        }
        {
          b = to_bytes($3)
          if (b > peak[$2]) peak[$2] = b
          tsum[$1] += b
          if (tsum[$1] > aggpeak) aggpeak = tsum[$1]
        }
        END {
          print "Per-container sampled peak:"
          for (c in peak) printf "  %-40s %.0f bytes\n", c, peak[c]
          printf "Aggregate sampled peak (sum of containers at the busiest sample): %.0f bytes\n", aggpeak
        }
      ' "$artifacts/memory.tsv"
    else
      echo "No samples recorded (no containers observed under this project during the run)."
    fi
  } > "$artifacts/memory.txt" 2>/dev/null || true

  return 0
}
