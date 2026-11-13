#!/usr/bin/env bash
# Tests for coverage-comment.sh (#129), against a fake gh. Run: .github/scripts/test-coverage-comment.sh
set -uo pipefail
cd "$(dirname "$0")"
fail=0
check() { if [ "$2" -eq 0 ]; then echo "ok   $1"; else echo "FAIL $1"; fail=1; fi; }
tmp=$(mktemp -d); trap 'rm -rf "$tmp"' EXIT; mkdir "$tmp/bin"
M='<!-- editrace-coverage -->'
printf '%s\n### Coverage\nok\n' "$M" > "$tmp/c.md"

# A fake gh: lists $FAKE_COMMENTS (a JSON array, paginated as two pages), logs every write.
cat > "$tmp/bin/gh" <<'FAKE'
#!/usr/bin/env bash
case "$*" in
  *"--paginate"*"/comments"*) [ -z "${FAKE_LIST_FAIL:-}" ] || exit 1; jq -c '.[:1]' "$FAKE_COMMENTS"; jq -c '.[1:]' "$FAKE_COMMENTS" ;;
  *"-X PATCH"*|*"-X POST"*) [ -z "${FAKE_WRITE_FAIL:-}" ] || exit 1; echo "$*" >> "$FAKE_LOG"; cat > "$FAKE_BODY" ;;
  *) echo "unexpected: $*" >&2; exit 1 ;;
esac
FAKE
chmod +x "$tmp/bin/gh"
export FAKE_LOG="$tmp/log" FAKE_BODY="$tmp/body" FAKE_COMMENTS="$tmp/comments.json"
run() { : > "$FAKE_LOG"; env PATH="$tmp/bin:$PATH" REPO=o/r PR=7 COMMENT_FILE="${COMMENT_FILE:-$tmp/c.md}" "$@" ./coverage-comment.sh > "$tmp/out" 2>&1; }
cm() { jq -cn --argjson id "$1" --arg l "$2" --arg b "$3" '{id: $id, user: {login: $l}, body: $b}'; }

echo '[]' > "$FAKE_COMMENTS"; run
[ "$?" -eq 0 ] && grep -q -- '-X POST repos/o/r/issues/7/comments' "$FAKE_LOG" && [ "$(wc -l < "$FAKE_LOG")" -eq 1 ] && [ "$(jq -r .body "$FAKE_BODY" | head -n 1)" = "$M" ]
check "no comment yet: one is posted" $?

jq -s . <<<"$(cm 10 someone 'hello')$(printf '\n')$(cm 11 'github-actions[bot]' "$M
old numbers")" > "$FAKE_COMMENTS"; run
[ "$?" -eq 0 ] && grep -q -- '-X PATCH repos/o/r/issues/comments/11' "$FAKE_LOG" && ! grep -q POST "$FAKE_LOG" && [ "$(jq -r .body "$FAKE_BODY" | sed -n 3p)" = ok ]
check "an existing comment (found on page 2) is updated in place, not duplicated" $?

jq -s . <<<"$(cm 10 mallory "$M
planted")" > "$FAKE_COMMENTS"; run
[ "$?" -eq 0 ] && grep -q -- '-X POST' "$FAKE_LOG" && ! grep -q PATCH "$FAKE_LOG"
check "a person's comment with the marker is never edited; a new bot comment is posted" $?

jq -s . <<<"$(cm 5 'github-actions[bot]' "$M
a")$(printf '\n')$(cm 9 'github-actions[bot]' "$M
b")" > "$FAKE_COMMENTS"; run
grep -q 'comments/9' "$FAKE_LOG"; check "if there are several bot comments, the newest is the one updated" $?

run FAKE_WRITE_FAIL=1; [ "$?" -eq 0 ] && grep -q '::warning::' "$tmp/out"
check "a failed write is a warning, not a failed gate" $?
run FAKE_LIST_FAIL=1; [ "$?" -eq 0 ] && [ ! -s "$FAKE_LOG" ] && grep -q "::warning::" "$tmp/out"; check "a failed listing posts nothing (no duplicate) and never fails the gate" $?
printf 'no marker\n' > "$tmp/bad.md"; COMMENT_FILE="$tmp/bad.md" run; [ "$?" -eq 2 ]
check "a comment without the marker is refused" $?
G='<!-- editrace-golden-eval -->'
printf '%s
### Golden eval
' "$G" > "$tmp/g.md"
jq -s . <<<"$(cm 11 'github-actions[bot]' "$M
coverage")$(printf '
')$(cm 12 'github-actions[bot]' "$G
old eval")" > "$FAKE_COMMENTS"; COMMENT_FILE="$tmp/g.md" run MARKER="$G"
[ "$?" -eq 0 ] && grep -q 'comments/12' "$FAKE_LOG" && ! grep -q 'comments/11' "$FAKE_LOG"
check "MARKER picks another sticky comment (the golden eval's) and leaves the coverage one alone" $?
exit $fail
