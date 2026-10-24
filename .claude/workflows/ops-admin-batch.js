export const meta = {
  name: 'ops-admin-batch',
  description: 'Build one batch of .claude/plans/ops-admin-tools.md: implement, adversarially review, fix',
  whenToUse: 'Resuming the ops-admin-tools plan; pass args {batch: "B1"} (one batch per run, in order)',
  phases: [
    { title: 'Implement', detail: 'probe live, TDD, specs, gates, commit, push' },
    { title: 'Review', detail: 'independent adversarial read of the batch commit' },
    { title: 'Fix', detail: 'address confirmed findings, re-run gates, amend via new commit' },
  ],
}

const PLAN = '.claude/plans/ops-admin-tools.md'
const batch = (args && args.batch) || null
if (!batch) throw new Error('pass args {batch: "B<n>"} — see ' + PLAN)

const RESULT = {
  type: 'object',
  properties: {
    status: { type: 'string', enum: ['committed', 'blocked'] },
    commit: { type: 'string', description: 'sha of the batch commit (pushed)' },
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
Read the plan first (the batch row, "Per-batch definition of done", and B0 if it is ${batch}).
Do the whole batch end to end: live probes against the dev stack (start + seed it if it is not up),
tests first, implementation, specs/docs/decision/CHANGELOG reconciled, gates green, ONE commit, push.
In the same commit mark the batch row [x] in the plan (no amend: the sha is added by the next commit)
and append one line to the plan's Log.
Tier decisions are yours under the repo rule; record the reasoning in the decision entry.
If a tool's API is not Basic or does not work live, drop it with evidence recorded in docs/roadmap.md.
Never use --no-verify. Return the structured result.`,
  { label: `implement:${batch}`, phase: 'Implement', schema: RESULT },
)
if (!impl || impl.status !== 'committed') return { batch, impl, review: null }

let rounds = 0
let review = null
while (rounds < 2) {
  phase('Review')
  review = await agent(
    `Adversarially review commit ${impl.commit} (batch ${batch} of ${PLAN}) in /home/user/mcp-for-kibana.
Read-only: do not edit files. Try to break it: wrong tier for the blast radius, error text that does not
name the fix, secrets echoed, read-modify-write that drops fields, missing space threading, missing or
weak live contract tests, docs/spec/CHANGELOG/decision drift vs the code, additive changes that alter
existing behavior. Only report findings with a concrete failing path or contradicted spec line.`,
    { label: `review:${batch}#${rounds + 1}`, phase: 'Review', schema: FINDINGS },
  )
  const blocking = (review && review.findings || []).filter(f => f.severity === 'blocking')
  log(`${batch} review round ${rounds + 1}: ${blocking.length} blocking, ${(review && review.findings || []).length - blocking.length} minor`)
  if (!blocking.length) break
  phase('Fix')
  const fix = await agent(
    `Fix these confirmed review findings for batch ${batch} (${PLAN}) on branch claude/ops-admin-tools:
${JSON.stringify(blocking, null, 2)}
Verify each before changing code; if one is wrong, leave it and say why. Re-run make check,
make test-contract and make test-e2e-replay; commit as a new fix(...) commit (no amend), push.`,
    { label: `fix:${batch}#${rounds + 1}`, phase: 'Fix', schema: RESULT },
  )
  if (fix && fix.commit) impl.commit = fix.commit
  rounds++
}

return { batch, impl, review }
