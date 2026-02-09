# Aegis AI — Branch Strategy

## Branch Layout

```
main                          <- stable, reviewed code only
  |
  +-- phase-4/task-nlp        <- Phase 4A: wire task NLP
  +-- phase-4/calendar        <- Phase 4B: local ICS calendar
  +-- phase-4/context         <- Phase 4C: operations context
  +-- phase-5/vram            <- Phase 5A: VRAM arbitration
  +-- phase-5/comfyui         <- Phase 5B: ComfyUI integration
  +-- phase-5/queue           <- Phase 5C: process queue
  +-- phase-6/tauri-ui        <- Phase 6: UI
  +-- phase-7/distribution    <- Phase 7: packaging
  +-- tests/foundation        <- test infrastructure
  +-- fix/<description>       <- bug fixes
  +-- feature/<description>   <- standalone features
```

## Workflow

### Before Sleeping (autonomous run)

1. Make sure main is committed and clean
2. Create the feature branch:
   ```powershell
   cd C:\Users\dusti\Projects\aegis-ai
   git checkout -b phase-4/task-nlp
   ```
3. Launch autonomous Claude session (see run-autonomous.ps1)
4. Go to sleep

### Morning Review

1. Check the log file in `autonomous-logs/`
2. Review what Claude did:
   ```powershell
   git log --oneline main..HEAD
   git diff main
   ```
3. If it looks good:
   ```powershell
   git checkout main
   git merge phase-4/task-nlp
   ```
4. If it's partial or messy:
   ```powershell
   # Cherry-pick the good commits
   git checkout main
   git cherry-pick <commit-hash>
   # Or reset the branch and redo
   git branch -D phase-4/task-nlp
   ```

### At Work (TeamViewer)

- Connect via TeamViewer
- Review progress, kick off another branch if needed
- Or run interactive Claude session for smaller tasks

## Rules

- **Never run autonomous sessions on main** — always a feature branch
- **One task per branch** — keeps diffs reviewable
- **Merge to main only after human review** — you are the gatekeeper
- **Delete branches after merge** — keep the tree clean

## Recommended Order

Run these in sequence (each builds on prior work):

1. **Initial commit** (on main) — get the baseline committed
2. **tests/foundation** — test infra so future phases can be validated
3. **phase-4/task-nlp** — wire the NLP patterns
4. **phase-4/calendar** — add local calendar
5. **phase-4/context** — tie operations into conversation context
6. **phase-5/vram** — VRAM management (independent of phase 4)
7. **phase-5/comfyui** — ComfyUI integration
8. **phase-5/queue** — process queue
