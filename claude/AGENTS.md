# Claude Code

- bypass sandbox to git commit: gpg agent access is blocked, do NOT try to start it
- bypass sandbox to git push: we use ssh, not HTTPS
- bypass sandbox to `gh`: token is stored in the keyring
- bypass sandbox to `terraform`: provider binaries need execution access
- git in non-current folders: prefer `git -C <dir>` over cd
