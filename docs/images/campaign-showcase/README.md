# Campaign showcase images

The presentation uses the Buffalo portfolio edition. The original poster PNGs,
campaign ZIP, and approval receipt are linked from the project README.

| Image | Source and treatment |
| --- | --- |
| `buffalo-formats.webp` · 1440 × 760 | Portrait and square PNGs from the [Buffalo portfolio](../../campaigns/buffalo/portfolio/REVIEW.md), scaled with their original aspect ratios in a presentation frame. The frame carries the format labels. |
| `campaign-review.webp` · 1440 × 1410 | Chromium capture of the actual approved Campaign panel at 1120 CSS pixels, framed and compressed for the README. |
| `campaign-review-mobile.webp` · 698 × 897 | Upper crop of the same Campaign panel at 393 CSS pixels showing the format controls and complete square preview. |

The capture ran against the public Buffalo snapshot in the disposable local app
with PostgreSQL. Chromium loaded both poster previews before submitting approval.
The PNG bytes displayed in the app matched the archived portrait and square.
The screenshot capture made zero paid requests and reported zero browser errors.

At 2× capture scale, the desktop panel decreased from 3364 to 1640 pixels tall.
The mobile panel decreased from 3492 to 2540 pixels, and the measured mobile
document fit the 393 CSS pixel viewport. These measurements cover the Buffalo
preview panel and the same selected square format.

[Campaign art provenance](../../campaigns/buffalo/portfolio/REVIEW.md) · [Local reproduction commands](../../../README.md#run-locally)
