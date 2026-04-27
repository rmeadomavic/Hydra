---
name: grill-me
description: Stress-test a plan or design by interviewing the user relentlessly until reaching shared understanding, resolving each branch of the decision tree. Use this skill whenever the user says "grill me", "stress test this design", "poke holes in this", "challenge my assumptions", or wants critical design review of any aspect of the project.
---

# Grill Me

Interview the user comprehensively about every aspect of their plan or design
until achieving shared understanding. Walk through each branch of the design
tree and resolve dependencies between decisions sequentially.

## Rules

1. **One question at a time.** Do not batch questions.
2. **Research before asking.** If a question can be answered by exploring the
   codebase, explore the codebase instead of asking the user.
3. **Recommend an answer.** For each question, provide your recommended approach
   with reasoning so the user can accept, reject, or modify it.
4. **Be relentless.** Keep probing until every branch is resolved. Don't accept
   vague answers — push for specifics.
5. **Prioritize by risk.** Start with safety-critical and hard-to-reverse
   decisions, then work toward lower-stakes items.
