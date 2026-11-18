**Open the comparison:** [side-by-side HTML](after/comparison.html) or [Markdown](after/comparison.md).

This is the archived November 17 comparison. The later bilingual changes, full offline test results, and remaining issues are described in the [November 18 review](../bilingual_validation_2026-11-18/README.md). The validation notes below describe the earlier run, before the stale tests were updated. Source snapshots are local ignored artifacts; baseline commit `8cc7e19` and the recorded hashes identify the original version.

The files show the same **51 customer messages** in both versions: 14 full conversations and six fixed-evidence response probes. Each conversation has a review note describing what improved, stayed the same, or became weaker. Arabic replies are preserved exactly as generated. The fixed welcome is also compared.

The original audit from earlier on 2026-11-17 is the baseline, copied to [before.json](before.json) before editing. Its source files and hashes are in `before_source/` and `before_source_hashes.json`. The final replay uses the same model (`openai/gpt-oss-120b`), response temperature (0.1), customer messages, test ticket fixtures and fixed knowledge passages. Both versions use mock tickets; no real tickets were submitted. Full conversations evolve independently, so workflow states and generated mock ticket numbers can differ.

The main changes are a shorter Saudi welcome, customary courtesy responses, active cancellation wording, less repeated detail collection, clearer handling of explanation requests and additional problems, and a configurable Arabic register per product. OLE5 has an explicit Saudi/Najdi register; RiCH keeps professional Arabic. The evidence check now explicitly checks added details inside otherwise supported sentences. The application changes are recorded in [changes.patch](changes.patch); [compare_voice.py](../../scripts/compare_voice.py) reruns and renders the comparison.

The improvement is **mixed, not universal**. Courtesy, cancellations, clarification and acknowledging another issue are stronger in this sample. Ticket lookups still contain formal wording and a typo, one technical answer remains formal, and the delivery overview loses useful detail. The notes flag these directly. A single before/after sample is not a statistical benchmark or a native-speaker assessment, and the fixed knowledge base does not test production retrieval.

A first edit was tested, reviewed and refined before the final replay. It is preserved in [first_pass/comparison.html](first_pass/comparison.html), including a clarification that added unsupported interface details. The final clarification stays within the supplied procedure. Both attempts remain available; the final comparison contains every original case, without removing weak results.

Validation:

- 38 response-language, response-guard and turn-sheet tests pass after the final edit.
- Two live evidence-check probes pass: invented menu/icon locations are rejected; documented watermark steps are accepted. See [guard_probes.json](guard_probes.json).
- Nine assertions in `tests/test_router_worker_context.py` fail against both the original source restored in an isolated copy and the first edit. They expect labels from the older combined router prompt. The tests were not changed to hide those failures.
- An initial full-suite run stalled after its first four tests and was interrupted. No full-suite pass is claimed.
- [validation.json](validation.json) records the checks. Application guards passing is not a Saudi voice score.

To replay the final code again, use a **new** output directory so the recorded comparison is preserved:

```bash
/home/m.sayed/miniconda3/envs/ole5-chat/bin/python scripts/compare_voice.py \
  --before reports/saudi_voice_comparison_2026-11-17/before.json \
  --output reports/saudi_voice_comparison_2026-11-17/another_run
```

This command makes live calls to the configured model. To only regenerate the files from already recorded replies, use `--render-only` with the existing output directory. Automatic wording flags in `after/review_flags.json` are limited review aids, not an overall quality grade.
