# ADR-0010 — answer verification: drop, flag or repair unsupported citations

- **Status:** proposed — not decided; the user decides between the options below
- **Date:** 2026-10-25
- **Decides:** what the server and the UI do when a quote cited in a chat answer fails the
  server-side check
- **Related:** ADR-0004 (model tiers and budget), ADR-0007 (citations are re-checked against
  `paper.txt`)

## Context

ADR-0007 says a quote that is not found "is not rendered as proof". It does not say whether
the claim stays in the answer. What happens today:

- **Ask mode.** The agent returns a structured `AskAnswer`: Markdown with `[n]` markers, one
  `Citation` per marker (work id, page, verbatim quote), and an `unsupported` list. After the
  run, `AskAgent.run` calls `portolan.agent.citations.verify_citations`. It looks for each quote
  in the work's `paper.txt` with `portolan.documents.text.find_passage`, then with relaxed
  matching (quote marks, ellipses, PDF line-break hyphens), and takes the hit nearest the
  claimed page. Page 0 means the abstract, and the quote is matched against it.
- **On a failed check, nothing is removed.** The citation stays in `VerifiedAnswer.citations`
  with `verified=False` and `source=None`, and `answer_markdown` is unchanged. A marker in the
  text that has no citation entry is added to `unsupported` as the string `"[n]"`.
- **UI.** `frontend/src/AnswerContent.tsx` shows an unverified chip with a dashed warning
  border and no link, labelled "quote not found in the paper". The sentence it is attached to
  looks the same as a verified one. The *Sources* list shows every citation, verified or not,
  with no mark. `unsupported` is shown under "Not supported by the project's papers".
- **Later turns.** The chat API stores `answer_markdown` as the message content, and
  `AskAgent._messages` sends it back as the assistant's earlier turn. The model sees its
  unverified claims again with nothing marking them as unverified.
- **Research mode is not checked.** `RESEARCH_PROMPT` says the mode "does not require citation
  verification", and the chat API stores only Research-mode Markdown, with no citations.
- **Cost.** Ask runs on `chat_model` (default `deepseek/deepseek-v4-pro`, ADR-0004's
  full-text tier) with a cap of `chat_max_tool_calls` (40). Checking quotes costs no tokens.
  No dollar ledger exists.

## Options

**A — Drop.** Remove each claim whose citations all failed. Show it in `unsupported` instead, in
the same answer.

- *For:* the strongest faithfulness. Every claim in the answer has a verified passage.
- *Against:* removing the right text from free Markdown is fragile. A marker can belong to a
  clause, a sentence or a list item, and removing only the marker leaves a claim with no
  citation, which is worse than a flagged one. A failure usually means the model paraphrased a
  quote whose finding is correct, so dropping it loses true content. Costs nothing.

**B — Flag (today's behaviour, made complete).** Keep the claim, mark it clearly, and make
the flag reach everything that uses the answer.

- *For:* nothing is lost, and the user can see what went wrong. It needs no extra model call.
- *Against:* the answer still asserts something unproven, and only the styling warns the reader.
  The gaps above need fixing first: the Sources list, the replay into later turns, and an
  answer-level note (for example "2 of 7 citations could not be verified").

**C — Repair, then flag.** When citations fail, give the same agent thread one repair turn
listing each failed citation (work, claimed page, quote). The agent must re-quote from
`paper.txt` or withdraw the claim into `unsupported`. Check again. Citations that still fail
fall back to B.

- *For:* it fixes the common failure, a near-miss quote from a paper that does support the
  claim, before the user sees it. Withdrawals are made by the model, which can rewrite the
  sentence properly, so no fragile text removal is needed.
- *Against:* extra latency, plus one more model turn when a failure happens. As an estimate, a
  repair turn that resends about 30k tokens of context and writes about 1k tokens costs about
  $0.03 at ADR-0004's v4-pro prices. That is small compared with the $10 ceiling, but it
  applies to each answer with a failure and is not counted anywhere. The extra turn needs a
  fixed cap (one repair pass, a few tool calls) so it cannot loop.

## Recommendation

**Option C with B as the fallback.** Allow one bounded repair pass. Show whatever still fails
with a clear flag in the chip, the Sources list and an answer-level note. Mark unverified
claims when the answer is replayed into later turns. Never delete text on the server. This is
the recommendation only. The user has not decided.

## Consequences (if accepted)

- `verify_citations` stays a pure check. The repair loop lives in `AskAgent.run`, and
  `VerifiedAnswer` records how many citations were repaired and how many withdrawn, so the
  effect can be measured.
- The repair turn counts towards `chat_max_tool_calls`, or gets its own small cap, so a single
  answer still has a fixed upper cost.
- Research mode stays unchecked by design. Whether its "what a paper claims" sentences should
  go through the same check is a separate follow-up.
- A test fixture with a deliberately near-miss quote and one fabricated quote covers repair,
  withdrawal and the fallback flag offline, using a fake model.
