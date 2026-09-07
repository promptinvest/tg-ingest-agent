# ADR-0010: Action-truth guard v3: tighten the bypass, carve out honest offers, align the prompt

- Status: Implemented 501b5f9
- Date: 2026-09-07
- Phase: Phase B — honesty and memory
- Source: 2026-09-07 review of architecture, code and live behavior against build e3208b8 (report kept off-repo; findings are referenced by id)

## Context

«Сохранила #51 в Movies, утром проверишь.» passes because any day-part word on the line counts as past; «Я бы добавила её в Movies — хочешь?» is blocked although the persona and repair prompts ask for exactly that shape; the prompt says "say you're on it" while pattern 8 blocks «беру в работу» and test 23740 pins the guard; «Убрала #58…» and «Я теперь не спрашиваю… записываю» are uncaught.

## Decision

subjunctive carve-out (pre-verb «(я )?бы …» tail and post-verb «бы»), disabled whenever a «Готово/done» header is on the line; day-part words disarm the guard only before the verb or when no clause boundary precedes them; add «убрала», «настроила», «больше не показываю/присылаю» shapes; rewrite CHARACTER:70-73 to "do not say you are on it or promise it — tell him exactly what to say and ask if he wants that", mirrored in CARA.md §4 and cara_persona.md; action_not_done and artifact_not_sent rewritten in her voice; fix the «обнim» cue; her-life exclusions limited to a named friend and «в плейлист»; every probed sentence becomes a regression test.

## Consequences

false positives drop, memory learning is no longer locked out by honest offers; tightening is the safe direction, loosening is done only for shapes that cannot assert completion.

## Resolves

converse-persona#1, #2, #3, #4, #5, #6, dialog-aug1#1 residue

## Depends on

nothing

## Status log

- 2026-09-07: proposed by the review; not yet discussed with the owner.
- 2026-09-07: implemented in `501b5f9` (Phase B batch) — subjunctive carve-out (`_subjunctive`: pre-verb «(я )?бы …» with ≤2 words between, post-verb «бы», never under a «Готово/done» head); day-part words excuse a claim only before/inside the match or after it with no clause boundary; «убрала/настроила» added to the narrative-capable set; a present-tense pattern for «я теперь не спрашиваю…» / «больше не показываю» / "I no longer ask"; her-life carve-out limited to `_HER_LIFE_RE` (Майя, «в плейлист»), soft verbs only; `converse.CHARACTER` no longer says "say you're on it" — she names the plain request and asks (mirrored in CARA.md §4 and cara_persona.md); `action_not_done`/`artifact_not_sent` rewritten in her voice; the «обнim» relational cue fixed; every probed sentence is a regression test (`PhaseB20260907Tests`).
