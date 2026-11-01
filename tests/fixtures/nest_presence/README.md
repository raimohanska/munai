# Public nest-presence regression images

These **24 committed JPEGs are entirely procedural**, not photographs or crops
from private cameras. No real names, measurements, textures, or reference labels
are included. `manifest.json` explicitly inventories their expected categories:

- 4 independent empty straw backgrounds (also used to calibrate the fake wall).
- 4 separated-egg scenes containing 1, 3, 9, and 15 drawn eggs.
- 5 touching/partially overlapping piles containing 3, 6, 9, 12, and 15 eggs.
- 11 abstract hen scenes: dark/light smooth plumage, coarse/fine feather patterns,
  a smaller patterned hen, clipping at every edge, a hen alongside separated eggs,
  and a smaller hen alongside a **larger** egg pile.

Tests load the actual committed files and fail if the inventory is missing or
changed unexpectedly. They exercise recognition, pre-sorting, offline egg-count
gating, and the HA prediction pipeline. Every pile must exceed the old chicken
area threshold without becoming a hen. Every hen must retain measurable features,
including the patterned examples that a thickness-only check would miss.
Mixed scenes verify hen priority and selection beyond the largest blob.

The manifest's `visible_eggs` is the number of drawn shells, **not a guarantee of
exact counting under occlusion**. Tests require exact counts for empty/separated
scenes, but only that piles reach the egg counter without triggering hen presence.

Regenerate deliberately (NumPy/OpenCV required):

```sh
python tools/generate_presence_fixtures.py
python -m unittest discover -s tests -p 'test_presence_images.py'
```

Do not add camera-derived images here. JPEG encoder versions may change bytes
slightly on regeneration; review image and test changes before committing them.
Synthetic regressions prove specific behaviors, not field accuracy. Independently
reviewed real images must still be checked locally before deploying.
