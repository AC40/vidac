# Editor handoff

The authoritative artifact is Vidac's saved edit plan. Exports translate it into editor interchange formats; they do not pretend to generate a proprietary project database.

| Export | Includes | Limitations |
| --- | --- | --- |
| Review MP4 | Ordered cuts, fit/letterbox, selected looks, source audio/silence | Uses proxies, capped at 960 pixels on the long edge. For review, not final delivery. |
| OpenTimelineIO | Media references, source ranges, video/audio tracks, markers and look metadata | Look metadata does not automatically become native grading nodes. Mixed-rate durations are quantized by the receiving editor. |
| FCPXML 1.10 | Original references, explicit timeline offsets/durations, conform-to-fit, audio mute, look-note markers | No native color-grade translation. Import and inspect in Final Cut Pro; iMovie is not supported. |
| Resolve handoff ZIP | OTIO, plan manifest, a standalone Resolve Python importer and instructions | No media is copied. Resolve scripting requires local setup. Live application compatibility must be validated on your Resolve installation. |

## DaVinci Resolve

1. Save your edit, choose **DaVinci Resolve bundle**, and export.
2. Download and extract the ZIP. Keep its files together on the editing machine.
3. Ensure source paths are online. Moving the bundle does not move or relink the media. A VFR asset points at its CFR conform under the workspace cache.
4. For a simple cut handoff, import `timeline.otio` through Resolve's timeline import command on a version with OTIO support. Verify the media links, frame rate, trims and audio.
5. For the scripted handoff, open Resolve Studio. Enable local external scripting in its preferences. Add the `Developer/Scripting/Modules` directory from **your installation** to Python's module path, following its bundled scripting README; Resolve may also require its scripting library environment variables.
6. Run `python import_to_resolve.py manifest.json` from the extracted folder using a Python environment that can import `DaVinciResolveScript`.

The script creates and saves a **new project**. It never loads an existing project for modification. It checks project settings, imports sources, creates a timeline, appends picture/audio at explicit positions, adds markers, and attempts simple node-1 CDL adjustments only for sources tagged with a Rec.709 transfer function. Missing/unsupported operations produce failures or printed warnings. Errors can leave a partial new project for inspection; there is no silent fallback to an existing one.

This is deliberately not a `.drp` generator. A native Resolve project can be saved/exported by Resolve after the import succeeds. The adapter's method calls are contract-tested with a test double; that is not a substitute for running it against the installed Resolve build.

## Final Cut Pro

Import `timeline.fcpxml` with originals available at the exported paths. Vidac preserves file URI escaping and rational rates, uses an asset resource per source, and creates one primary storyline. Muted clips use a volume adjustment. The look name appears as a marker note; apply or refine the grade in FCP.

FCPXML is generated and structurally tested, but live Final Cut import has not been tested in this Linux development environment. In particular, check sources with nonzero timecode, mixed frame rates, unusual audio layouts, HDR and anamorphic pixels before trusting a long sequence. There is no iMovie project export.

## Color and framing

The four presets are modest display-referred CDL starting points: neutral, warm, cool and monochrome. These are **not** automatic exposure balancing, camera matching, color-managed log transforms or scene-referred grades. The proxy renderer applies them to review images. The Resolve script skips unknown/log/HDR transfer functions and reports that choice; OTIO/FCPXML carry look information for manual finishing.

Portrait/square exports currently fit the source into the canvas, potentially with letterboxing. There is no subject-aware crop. Review the project color-management settings and source input transforms in the receiving editor before grading.

## Acceptance checklist

- Confirm no source or conform is offline.
- Compare first/last frame at each cut against the review render.
- Check timeline rate, mixed-rate motion and the total running time.
- Check audio alignment, muted sections, channel layout and transitions.
- Review rotation, framing, HDR/log normalization and every suggested grade.
- Save a native editor project and back up its referenced media.

Do a short pilot with your actual cameras before adopting any interchange workflow for a large library.
