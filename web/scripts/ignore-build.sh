#!/bin/sh
# Vercel's Ignored Build Step (`ignoreCommand` in vercel.json), run in web/: exit 0 skips the
# deployment, exit 1 builds it.
#
# Skip only when web/ is unchanged since the last successful deployment of this branch, which
# Vercel passes as VERCEL_GIT_PREVIOUS_SHA. Comparing with HEAD^ instead would strand a web change
# whose deployment failed as soon as an engine-only commit followed it. Build whenever that
# baseline can't be used: on a branch's first deployment (it is empty), when it is outside
# Vercel's shallow clone, and on a redeploy of the deployed commit (it equals HEAD).

base=${VERCEL_GIT_PREVIOUS_SHA:-}
if [ -z "$base" ]; then
  echo "ignore-build: no earlier successful deployment of this branch, building"
  exit 1
fi
if ! git cat-file -e "$base^{commit}" 2>/dev/null; then
  echo "ignore-build: last deployed commit $base is not in the clone, building"
  exit 1
fi
if [ "$(git rev-parse "$base^{commit}")" = "$(git rev-parse HEAD)" ]; then
  echo "ignore-build: redeploying $base, building"
  exit 1
fi
if git diff --quiet "$base" HEAD -- .; then
  echo "ignore-build: web/ unchanged since $base, skipping"
  exit 0
fi
echo "ignore-build: web/ changed since $base, building"
exit 1
