#!/usr/bin/env bash
# Finds an existing PR comment by an HTML marker and edits it in place; posts a new one only
# if none exists yet. CLAUDE_CODE_PLAN.md Session 8: "never post a second." Requires
# GITHUB_TOKEN (pull-requests: write), PR_NUMBER, COMMENT_BODY_FILE, and COMMENT_MARKER in the
# environment — see action.yml's "Upsert PR comment" step. COMMENT_MARKER is per-product (e.g.
# "<!-- foretop:ebb:pr-comment -->") so a repo running more than one foretop Action on the same
# PR upserts each product's own comment instead of colliding on one. GITHUB_REPOSITORY and
# GITHUB_API_URL are provided automatically by the Actions runner.
set -euo pipefail

MARKER="${COMMENT_MARKER:?COMMENT_MARKER must be set to this product's own HTML comment marker}"
API="${GITHUB_API_URL:-https://api.github.com}"

if [ -z "${PR_NUMBER:-}" ]; then
  echo "No PR_NUMBER in the environment — not a pull_request event, skipping comment upsert."
  exit 0
fi

body="$(printf '%s\n\n%s\n' "$MARKER" "$(cat "$COMMENT_BODY_FILE")")"
payload="$(jq -n --arg body "$body" '{body: $body}')"

auth_header="Authorization: Bearer ${GITHUB_TOKEN}"
accept_header="Accept: application/vnd.github+json"
version_header="X-GitHub-Api-Version: 2022-11-28"

# Only the first 100 comments are considered — a PR with more than that is not this script's
# realistic case, and the worst-case failure mode is one extra comment, not a security issue.
existing_id="$(curl -sSf \
  -H "$auth_header" -H "$accept_header" -H "$version_header" \
  "${API}/repos/${GITHUB_REPOSITORY}/issues/${PR_NUMBER}/comments?per_page=100" \
  | jq -r --arg marker "$MARKER" '[.[] | select(.body | startswith($marker))][0].id // empty')"

if [ -n "$existing_id" ]; then
  curl -sSf -X PATCH \
    -H "$auth_header" -H "$accept_header" -H "$version_header" \
    "${API}/repos/${GITHUB_REPOSITORY}/issues/comments/${existing_id}" \
    -d "$payload" > /dev/null
  echo "Updated existing PR comment ${existing_id}."
else
  curl -sSf -X POST \
    -H "$auth_header" -H "$accept_header" -H "$version_header" \
    "${API}/repos/${GITHUB_REPOSITORY}/issues/${PR_NUMBER}/comments" \
    -d "$payload" > /dev/null
  echo "Posted a new PR comment."
fi
