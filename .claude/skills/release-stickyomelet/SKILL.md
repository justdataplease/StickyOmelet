---
name: release-stickyomelet
description: Prepare, verify, tag, and publish portable StickyOmelet Windows releases. Use when creating a new StickyOmelet version, release build, Git tag, GitHub Release, release notes, or validating that the portable EXE is safe to publish.
---

# Release StickyOmelet

Produce one release artifact: `dist\StickyOmelet.exe`. Do not create or publish setup, MSI, MSIX, certificate, settings, or credential files.

## Release workflow

1. Inspect `git status`, the current branch, and the complete pending diff. Preserve unrelated user changes.
2. Choose a SemVer version. Update every file and product version in `assets/version_info.txt` to the same four-part Windows version.
3. Update user documentation and tests for changed behavior. Keep the README portable-only.
4. Run the deterministic verification script:

   ```powershell
   .\.claude\skills\release-stickyomelet\scripts\prepare_release.ps1 -Version X.Y.Z
   ```

5. Launch `dist\StickyOmelet.exe`. Verify the window title, responsiveness, Google Keep connection, theme, compact search, autosave, startup toggle, list editing, and window resizing. Do not expose note content or credentials in logs.
6. Review the final diff and credential-pattern scan. Ensure `.gitignore` excludes `dist`, settings, certificates, caches, and build output.
7. Commit the reviewed source with `Release vX.Y.Z` and create annotated tag `vX.Y.Z` only when the user has authorized a release.
8. Publish only when a GitHub remote exists and GitHub authentication succeeds. Upload `dist\StickyOmelet.exe`, include its SHA-256 in the notes, and disclose that an unsigned community build can trigger SmartScreen.

## Safety rules

- Never request, print, commit, or upload a Google token, cookie, email, device ID, settings file, or note content.
- Keep browser and Google calls mocked in automated tests.
- Never overwrite an existing remote tag or release unless the user explicitly requests it.
- Never claim GitHub publication succeeded without checking the remote release URL.
- Stop before publishing if tests, version checks, launch checks, or credential scanning fail.
- Treat missing GitHub authentication or remote configuration as a publishing blocker, not a reason to skip verification.

## GitHub release command

After a clean tagged commit and successful `gh auth status`:

```powershell
gh release create vX.Y.Z .\dist\StickyOmelet.exe --title "StickyOmelet X.Y.Z" --notes-file .\release-notes.md
```

Delete temporary release notes after publication unless the project intentionally tracks them.
