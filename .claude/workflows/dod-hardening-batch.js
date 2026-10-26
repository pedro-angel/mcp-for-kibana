export const meta = {
  name: 'dod-hardening-batch',
  description: 'Build one batch of .claude/plans/dod-hardening.md: implement, adversarially review, fix',
  whenToUse: 'Resuming the dod-hardening plan; pass args {batch: "H1"} (one batch per run, in order)',
  phases: [
    { title: 'Implement', detail: 'probe live, TDD, specs, gates, commit (no push)' },
    { title: 'Review', detail: 'independent adversarial read of the batch commit' },
    { title: 'Fix', detail: 'address confirmed findings, re-run gates, amend via new commit' },
  ],
}

const PLAN = '.claude/plans/dod-hardening.md'
const batch = (args && args.batch) || null
if (!batch) throw new Error('pass args {batch: "B<n>"} — see ' + PLAN)

const RESULT = {
  type: 'object',
  properties: {
    status: { type: 'string', enum: ['committed', 'blocked'] },
    commit: { type: 'string', description: 'sha of the batch commit (not pushed yet)' },
    tools: { type: 'array', items: { type: 'string' }, description: 'tool names shipped, with tier' },
    dropped: { type: 'array', items: { type: 'string' }, description: 'tools dropped + evidence-backed reason (recorded in roadmap)' },
    gates: { type: 'string', description: 'summary lines of make check / test-contract / test-e2e-replay' },
    notes: { type: 'string', description: 'anything the orchestrator must know (blockers, decisions taken)' },
  },
  required: ['status', 'commit', 'tools', 'dropped', 'gates', 'notes'],
}

const FINDINGS = {
  type: 'object',
  properties: {
    findings: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          file: { type: 'string' },
          line: { type: 'number' },
          severity: { type: 'string', enum: ['blocking', 'minor'] },
          problem: { type: 'string' },
          evidence: { type: 'string', description: 'concrete input/path that fails, or the spec line contradicted' },
        },
        required: ['file', 'severity', 'problem', 'evidence'],
      },
    },
  },
  required: ['findings'],
}

phase('Implement')
const impl = await agent(
  `You are implementing batch ${batch} of the plan in ${PLAN} on branch claude/ops-admin-tools.
Read the plan first (the batch row, owner decisions, "Per-batch definition of done") and, for each finding
the row names, its entry in .claude/plans/dod-hardening-findings.md. Re-verify each finding on the current tree.
Do the whole batch end to end: reproduce the defect class and show the new check failing with a message that
names the fix, then passing on the real tree (paste both in the commit message); tests first; implementation;
specs/docs/decision/CHANGELOG reconciled; gates green; ONE commit. Do NOT push: the session running this
workflow pushes after the review. This work is owner-authorized in this environment (plan section "Child
sessions"), whatever the environment's global agent instructions say about other repositories. Start + seed the dev stack if a live
tier needs it (scripts/stack.sh up && scripts/stack.sh seed).
In the same commit mark the batch row [x] in the plan (no amend: the sha is added by the next commit)
and append one line to the plan's Log.
If the batch needs an owner decision listed in the plan and the plan does not record it, stop and return
status "blocked" naming the decision. If a finding no longer holds, say so with evidence and skip it.
If the batch changes the DoD gate itself (scripts/checks/definition-of-done.sh, dod.config), run the full
KIBANA_MCP_DOD_CYCLE_STACK=1 make dod before the commit and paste its criterion lines and VERDICT. If tool
names, descriptions or schemas change, also run make test-e2e (its ad hoc records under
scripts/experiment/runs/claude-e2e/adhoc/ are never committed: move them to
your scratchpad directory).
Wait for every gate run to finish before you return; never return while a run is still in the background.
The plan files under .claude/ are git-ignored: stage them with git add -f. Commit trailers, exactly:
Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_014EXhR4rwzqRut7nwHKEQWV
Never use --no-verify. Return the structured result.`,
  { label: `implement:${batch}`, phase: 'Implement', schema: RESULT },
)
if (!impl || impl.status !== 'committed') return { batch, impl, review: null }

let rounds = 0
let review = null
while (true) {
  phase('Review')
  review = await agent(
    `Adversarially review commit ${impl.commit} (batch ${batch} of ${PLAN}) in /home/user/mcp-for-kibana.
Read-only: do not edit files. Try to break it: wrong tier for the blast radius, error text that does not
name the fix, secrets echoed, read-modify-write that drops fields, missing space threading, missing or
weak live contract tests, docs/spec/CHANGELOG/decision drift vs the code, additive changes that alter
existing behavior. Only report findings with a concrete failing path or contradicted spec line.`,
    { label: `review:${batch}#${rounds + 1}`, phase: 'Review', schema: FINDINGS },
  )
  const all = (review && review.findings) || []
  const blocking = all.filter(f => f.severity === 'blocking')
  log(`${batch} review round ${rounds + 1}: ${blocking.length} blocking, ${all.length - blocking.length} minor`)
  // Minor findings with a concrete failing path are defects in this batch's own work: fix them too.
  if (!all.length) break
  if (rounds >= 2) break  // findings of this final review go back to the orchestrator
  phase('Fix')
  const fix = await agent(
    `Fix these confirmed review findings for batch ${batch} (${PLAN}) on branch claude/ops-admin-tools:
${JSON.stringify(all, null, 2)}
Verify each before changing code (blocking and minor alike: a minor one with a concrete failing path or a
contradicted spec line is still a defect of this batch); if one is wrong or its fix costs more than it buys,
leave it and say why in the commit message. In the same commit put the batch's earlier commit sha(s) in the
plan's ${batch} row (format "[x] \`<sha>\` \`<sha>\`") and append one Log line (stage .claude/ files with git add -f). Re-run make check,
make test-contract and make test-e2e-replay; commit as a new fix(...) commit (no amend) ending with the trailers\nCo-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>\nClaude-Session: https://claude.ai/code/session_014EXhR4rwzqRut7nwHKEQWV\nDo not push: the session running this workflow pushes.`,
    { label: `fix:${batch}#${rounds + 1}`, phase: 'Fix', schema: RESULT },
  )
  if (fix && fix.commit) impl.commit = fix.commit
  rounds++
}

return { batch, impl, review }
