# Plans

Work-in-flight tracking per the SpecOps plan protocol — one file per scope-bounded
chunk of work, with status/dependency frontmatter forming a micro-DAG.

Protocol reference: [.agents/skills/specops/references/plans-protocol.md](../.agents/skills/specops/references/plans-protocol.md)

Ad-hoc queries:

- In flight: `grep -l '^status: in-progress' plans/*.md`
- Done: `grep -l '^status: done' plans/*.md`
