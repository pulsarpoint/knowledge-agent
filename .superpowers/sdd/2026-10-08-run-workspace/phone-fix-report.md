
# Phone fix: Test tab pane collapse
Changes: RunWorkspace run grid `min-h-48`; useFillViewport Math.floor, floor default 20rem -> 26rem (header + message box + 12rem pane fit; the fill height remains the target, 1366x768 gives far more than 26rem so the laptop layout is unchanged), also observes element.parentElement.
Failing evidence (before impl): floor test got 676px expected 675px; parent test: observed lacked parent; floor test 320px vs 416px; RunWorkspace class test received "grid min-h-0 flex-1 ..." expected min-h-48.
Result: vitest 46 files / 357 tests pass; tsc clean.
Concerns: the 26rem floor is an estimate, a wrapped header with alerts can still spill by a little on a very small phone; not verified in a real browser.
