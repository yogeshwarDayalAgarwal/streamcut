# StreamCut engineering notes

Measurements and decisions behind the current download pipeline. Useful when
YouTube changes something and this needs revisiting.

## Why the download step is split in two

`yt-dlp --download-sections` was the original approach. That flag forces the
ffmpeg downloader, and ffmpeg cannot use yt-dlp's parallel fragment
downloading. Measured on the same video and format:

| Downloader | Throughput |
| --- | --- |
| `--downloader ffmpeg` (what `--download-sections` forces) | ~185 KB/s |
| `--downloader native` (default) | ~3.4 MB/s |

That is roughly an 18x difference. So the pipeline now:

1. Downloads the source with yt-dlp's **native** downloader.
2. Cuts the segment with a separate **ffmpeg** pass.

A 60-second 1080p clip went from ~91s to ~37s end to end.

## Why we re-encode instead of stream-copying

`ffmpeg -c copy` is far faster (~0.15s) but only lands correctly when both the
start and end of the range fall exactly on keyframes. A sweep across ten
random ranges showed copied clips could:

- start late for fractional starts (SSIM ~0.88 at `17.3s` and `103.7s`), and
- truncate the tail (SSIM ~0.70-0.90 at the end frame).

Both cases drop requested frames, which is unacceptable for an editor who
expects the named range to be present. Re-encoding keeps both boundaries
frame-accurate, so the requested range is always fully contained.

Verified frame accuracy on real API output (SSIM, higher is better):

| Comparison | SSIM |
| --- | --- |
| clip@0s vs source@60.00s | 0.989 |
| clip@59s vs source@119.00s | 0.992 |
| clip@0s vs source@60.04s (next frame, control) | 0.940 |

The control being clearly worse is what proves the cut starts on the exact
requested frame rather than one before or after.

## Encoder choice

Measured for a 60-second 1080p cut. All produced exactly 1500 frames at the
source's 25fps, so none of them drop frames:

| Preset | Time | Size |
| --- | --- | --- |
| `veryfast` crf20 | 34s | 19 MB |
| `ultrafast` crf20 | 14s | 40 MB |
| `ultrafast` crf23 | 9s | 28 MB |
| stream copy | 0.15s | lossless, but boundaries drift |

`ultrafast` crf20 is the current default: roughly 2.5x faster than `veryfast`
with the same frame count, which matters most on Render's free-tier CPU.
Reduce crf to 23 if size matters more than quality on large jobs.

## yt-dlp version requirement

Downloads failed with **HTTP 403 Forbidden** from YouTube's media CDN while
metadata extraction still worked, which made it look like a PO token problem.
It was not. The deciding factor is the yt-dlp version:

| yt-dlp version | Result |
| --- | --- |
| 2026.06.09 | 403 Forbidden |
| 2026.08.19 | works |

Both versions reported Deno as an available JS runtime and logged the same
`jsc:deno` challenge solving, so the older build was simply running an outdated
solver. requirements.txt pins `yt-dlp>=2026.8.19` for this reason. When YouTube
breaks the download step again, bump that pin rather than adding workarounds.

## "Failed to extract any player response" (datacenter IPs)

A separate failure seen on Render, not locally:

```
ERROR: [youtube] <id>: Failed to extract any player response
```

This is environmental. YouTube serves different responses per player client,
and from datacenter IPs one client can fail entirely while another succeeds.
`ytdlp_metadata()` in `main.py` handles it by trying the default client first
and only falling back to alternates (`web,mweb,tv` then `android,web`) when the
error text looks like a player-response or bot-check problem. Other errors
(private, unavailable) fail fast without retrying, since a different client
would not help.

The fallbacks make extraction resilient but are not a guarantee. If YouTube
starts hard-blocking Render's IP range, the durable fixes are a proxy
(`--proxy`) or running the app on a different host. The `/api/info` error
message says as much when this happens.

## Portrait / vertical videos

yt-dlp reports the **longer edge** as `height`. A 1080x1920 Short therefore
arrives with `height=1920`, which the original logic would have advertised as
"4K" and matched with a `height=720` selector that grabbed the wrong stream.

Both `/api/info` and `/api/download` now work off the **short edge**:

- `/api/info` computes `min(width, height)` and buckets it to 720/1080/1440/2160,
  and returns a `vertical` flag the UI shows as "Vertical".
- `/api/download` caps the short edge (`height<=N` for landscape, `width<=N` for
  portrait) so a vertical 1080p request gets 720x1280, not 480x854 or 1080x1920.

Orientation is read from the `/api/info` cache (`INFO_CACHE`) when present, so a
download does not repeat the metadata extraction.

## Docker / Render notes

- The image installs Deno because yt-dlp needs a JavaScript runtime to solve
  YouTube's challenge. Metadata can succeed without it while downloads fail.
- `requirements.txt` is copied and installed before the rest of the source, so
  application edits do not invalidate the dependency layer.
- Render's filesystem is ephemeral. Finished clips only need to live long
  enough for the browser to fetch them.
- The free plan sleeps after idle periods and has limited CPU. The first
  request after a sleep pays a cold-start cost, and long or high-resolution
  jobs can be slow. Paid instances or a larger plan are worth considering if
  jobs regularly exceed a few minutes.
