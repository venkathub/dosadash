# RAG refusal judge correction

The prior GitHub run rejected two appropriate limitations: the assistant lacked
information about pizza (`rag:ans-004`) and coupon codes (`rag:ans-006`). In one
verdict it treated limited knowledge as an unsupported restaurant fact. In the
other, its evidence-answerability boolean contradicted its explanation that the
refusal was appropriate. The exact replies, evidence and original failures are
preserved in `golden/faithfulness_regressions.jsonl`, with the originating run URL.

## Correction

`faithfulness_v4` separates evidence answerability, speech-act classification and
factual grounding. The evidence stage cannot see the answer or refusal flag;
the classifier cannot see evidence. Only factual units reach the grounding stage.
Every answer unit must still be accounted for, and mixed units containing any
factual assertion remain subject to grounding. Refusal correctness is calculated
once from the independent evidence decision and actual response flag. No second
model boolean can contradict that calculation.

Each stage receives the JSON schema generated from its Pydantic response model.
The shared client requests JSON mode but does not transmit that schema itself.
An initial validation attempt omitted the schema and produced missing/wrong
field errors; its report is retained separately. The schema correction was made
while that old-process run was finishing, so it is explicitly not a frozen-source
quality baseline. The corrected validation uses unchanged source and assets.

No calibration label, quality threshold, generation prompt or model was changed.
Structural coverage/index errors permit one bounded repair per stage; semantic
quality failures are not retried. Tests retain rejection of invented benefits,
unsupported policy claims inside refusals, and refusal of answerable questions.

## Verification

- **200 key-free evaluation tests passed**, including isolation between judge stages,
  exact unit coverage, bounded repair and rejecting invented factual tails.
- [Corrected live validation](faithfulness-refusal-validation-final.json): **22/22**
  approved calibration labels agreed, **10/10** targeted regressions passed (five
  repetitions of each exact failed reply), and **12/12** retained RAG answers passed.
  Zero execution errors; source and asset hashes unchanged during the run.
- 97 provider attempts, 117,877 tokens, estimated cost
  **$0.107922**, below the $0.50 known-cost ceiling.
- [Initial schema-failure report](faithfulness-refusal-validation.json) is retained
  as implementation diagnostic evidence, not a quality baseline.

The live replay kept answers and evidence fixed; it did not regenerate favorable
answers or retry failed quality verdicts. Pure refusal cases used evidence and
classification stages only; answers with factual assertions also used grounding.
`judge_attempts` counts stage invocations; the usage ledger separately records
provider calls, including JSON validation repairs. A fresh end-to-end result is
available through the PR's live CI check and its uploaded artifact.
