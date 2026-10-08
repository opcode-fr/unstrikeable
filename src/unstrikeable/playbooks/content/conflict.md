The PR of this post no longer merges cleanly.

1. `uns move <ref> doing`, rebase the branch on the default branch, resolve, `git push --force-with-lease`.
2. Comment "rebase only" (or what changed), then `uns move <ref> approved` if the text did not change,
   `uns move <ref> review` if it did.
