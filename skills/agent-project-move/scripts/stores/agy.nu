# ==================================================================================================
# Agent Project Move: Antigravity CLI
# ==================================================================================================

use ../lib/actions.nu [action prepared-file-action sql-action]
use ../lib/paths.nu [home-dir]
use ../lib/selection.nu [scan-sessions select-sessions]

const STORE = path self "agy_store.py"

# --------------------------------------------------------------------------------------------------
# Store
# --------------------------------------------------------------------------------------------------

def root []: nothing -> string { home-dir | path join ".gemini/antigravity-cli" }

def run-store [command: string, location: string, params: record]: nothing -> any {
  let result = (
    $params
    | to json --raw
    | ^python3 -B -E -s $STORE $command $location
    | complete
  )
  if $result.exit_code != 0 {
    error make --unspanned {
      msg: $"Antigravity CLI: ($result.stderr | str trim)"
    }
  }
  $result.stdout | from json
}

def sessions []: nothing -> list<record> {
  let location = (root)
  if not ($location | path exists) { return [] }
  run-store "sessions" $location {}
}

def actions [plan: list<record>]: nothing -> list<record> {
  $plan
  | each {|row|
    match $row.op {
      "sql" => {
        let derived = ($row | get -o derived | default false)
        let rows_first = ($row | get -o rows_first | default false)
        (
          sql-action "agy" $row.store $row.path $row.sql $row.description
          --derived=$derived --rows-first=$rows_first
        )
      }
      "prepared" => {
        let preparation = $row.preparation
        let prepare = {|temporary| run-store "prepare" $temporary $preparation | ignore }
        prepared-file-action "agy" $row.store $row.path $row.description $prepare --to $row.to
      }
      "delete" => { action "agy" $row.store "delete" $row.path $row.description }
    }
  }
}

# --------------------------------------------------------------------------------------------------
# Scan and Move
# --------------------------------------------------------------------------------------------------

export def scan []: nothing -> list<record> {
  sessions
  | update last_used {|row| $row.last_used | into datetime }
  | scan-sessions "agy" --recorded-time
}

export def plan-move [
  old: string
  new: string
  --exact
  --session-ids: list<string> = []
]: nothing -> list<record> {
  let all = (sessions)
  let rows = if $exact or ($session_ids | is-not-empty) {
    select-sessions $all $old --exact=$exact --session-ids $session_ids
  } else {
    $all
  }
  if ($rows | is-empty) { return [] }
  let params = {
    rows: $rows
    old: $old
    new: $new
  }
  actions (run-store "move" (root) $params)
}

# --------------------------------------------------------------------------------------------------
# Transfer
# --------------------------------------------------------------------------------------------------

export def export-bundle [
  old: string
  --exact
  --session-ids: list<string> = []
]: nothing -> record {
  let rows = (select-sessions (sessions) $old --exact=$exact --session-ids $session_ids)
  if ($rows | is-empty) {
    return {
      assistant: "agy"
      old: $old
      files: []
      tables: []
      sessions: []
    }
  }
  let params = {
    rows: $rows
    old: $old
  }
  let bundle = (run-store "export" (root) $params)
  let files = (
    $bundle.files
    | each {|file|
      if $file.kind != "conversation" { return $file }
      let preparation = {
        source: $file.source
        sql: []
      }
      let prepare = {|target| run-store "prepare" $target $preparation | ignore }
      $file | merge {prepare: $prepare}
    }
  )
  $bundle | upsert files $files
}

export def plan-import [bundle: record, --target: string]: nothing -> list<record> {
  let params = {
    bundle: $bundle
    target: $target
  }
  actions (run-store "import" (root) $params)
}

# --------------------------------------------------------------------------------------------------
# Retirement
# --------------------------------------------------------------------------------------------------

export def plan-retire [
  path: string
  --exact
  --session-ids: list<string> = []
]: nothing -> list<record> {
  let rows = (select-sessions (sessions) $path --exact=$exact --session-ids $session_ids)
  if ($rows | is-empty) { return [] }
  actions (run-store "retire" (root) {rows: $rows})
}
