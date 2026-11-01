# Image experiment handoff

## Session decisions (2026-10-28)

The project captures timestamped overhead nest images from a LAN RTSP camera.
The eventual goal is to detect egg-laying events and identify which of the configured
hens produced each egg.

The user initially requested a plan to evaluate ready-made detectors for eggs
and overhead chickens, with a Python 3.10 devcontainer supporting inference,
training, and OpenCV image processing. They then proposed a simpler first step:

> Detect an empty nest, a chicken (a very big blob), or an egg (smooth and oval-like),
> as a pre-classifier for putting images into three baskets.

The latest instruction is to create **only the devcontainer and this handoff**.
Implement and run the classifier in the next session, inside the container.
Do not install packages or perform further Python work on the host.
No classifier or model inference has been implemented or run yet.

## Environment and boundaries

- Open the project with VS Code's **Dev Containers: Reopen in Container**.
- Python 3.10, headless OpenCV, NumPy, Pillow, matplotlib, JupyterLab, and
  ipykernel are installed by `.devcontainer/Dockerfile`.
- OpenCode 2.2.0 (the latest official release checked during setup) is installed
  using the official V2 standalone installer, pinned by the Dockerfile's
  `OPENCODE_VERSION` build argument. Run `opencode` inside the container to
  continue. Authenticate your provider there; host credentials are not copied.
  Rebuild/reopen the container to pick up this addition.
- Headless OpenCV means write preview images or use notebooks, not `cv2.imshow`.
- The workspace is bind-mounted; edits persist on the host. Model caches use a
  named Docker volume. There is no GPU requirement for this first stage.
- The Docker build context is only `.devcontainer/`, with an allowlist, so no
  captures or credentials are copied into the image.
- The workspace mount still includes local files: **never read, inspect, source,
  print, copy, or expose `.env`**, even from inside the container. No env file
  should be loaded for offline image experiments.
- Leave `capture.py` unchanged. Its Python 3.5.3/Pi requirements are independent
  of the Python 3.10 experiment environment. Follow `AGENTS.md` for capture work.
- Do not write automated camera-capture tests. Tests of independent image
  classification helpers are a separate concern.

## Local observations

Keep real capture observations, filenames, and camera calibration outside Git.

## Next experiment: three-basket heuristic

### Goal

Make an inexpensive, interpretable local pre-sorter, **not** a reliable egg
counter, hen identifier, or laying-event detector. No model downloads required.
The algorithm below is a proposal to calibrate, not an already validated method.

### Proposed approach

1. Enumerate timestamped JPEGs only; ignore hidden/temp files. Create a contact
   sheet and check sample diversity before choosing thresholds.
2. Downsample for speed while preserving enough pixels across an egg. Measure
   this tradeoff on a locally reviewed egg sample rather than assuming 640 pixels is
   sufficient.
3. Compute local texture/gradient statistics with OpenCV. Straw tends to be
   textured; an egg tends to have a smoother interior and oval boundary.
4. Investigate a large-region rule for chicken presence using area, texture,
   and optionally difference from manually selected empty reference frames.
   Feather texture, edge truncation, and shifting straw may defeat simple rules.
5. Investigate an egg rule using smooth connected regions, expected size,
   contours/ellipse fit, aspect ratio, and boundary support. Do not rely on
   brightness alone. Do not assume eggs can occur only at one position.
6. Handle image borders and overlays explicitly without masking away any
   visible eggs. Keep preprocessing configurable and inspect masks.
7. Assign one basket: `chicken`, `egg`, or `empty`. Proposed precedence:
   chicken wins if both chicken and egg are detected; otherwise egg, otherwise
   empty. Confirm this policy with the user. Preserve separate candidate flags
   so a simultaneous visible egg is not lost from the manifest.

### Outputs and safety

- Preserve all originals in `captures/`: never move, rename, or overwrite them.
- Proposed output: `artifacts/presort/<run-id>/`, containing basket directories,
  a CSV/JSON manifest, and selected diagnostic previews.
- Prefer links to originals rather than copying every JPEG. Make link behavior
  explicit and keep it within the mounted workspace so links work on the host.
- Record source filename, basket, candidate flags, raw heuristic scores,
  parameters, and reasons. Scores are not calibrated probabilities.
- Write previews showing candidate contours/regions and masks, not just labels.
- Reruns must not overwrite earlier results; partial failures should be reported.
- A forced three-way split will misclassify ambiguous images. Flag borderline
  decisions in the manifest for review rather than silently implying certainty.
- Add generated output paths to `.gitignore` when implementation begins.

### Evaluation

Manually review a small diverse sample before processing the whole collection.
Include empty straw, clear/partially covered eggs, different hens, clipped hens,
and mixed hen/egg scenes where available. Report a confusion matrix and inspect
false egg and false empty decisions. Tune on development images; hold out other
days or nesting visits. Adjacent images of the same scene are not independent.
If texture/shape rules fail, document the failure rather than endlessly adding
fragile thresholds. Use the later model comparison as the fallback.

## Later stage: pretrained detection (not installed yet)

The lightweight container intentionally does not yet install PyTorch, Transformers,
Ultralytics, or download weights. Add Python 3.10-compatible pinned dependencies
inside the container when this stage is authorized. Choose matching torch and
torchvision builds for the actual CPU/CUDA platform; run import, inference, and
short training smoke checks there. macOS Docker cannot use Apple's MPS backend;
GPU acceleration would need an appropriate Linux/NVIDIA host and configuration.

Candidates researched:

- Grounding DINO Tiny: pretrained open-vocabulary boxes, text prompts such as
  `an egg. a chicken.`; Hugging Face Transformers integration; Apache-2.0 model
  card: https://huggingface.co/IDEA-Research/grounding-dino-tiny
- YOLOE small: open-vocabulary text and visual-example prompting, boxes and
  masks. Check exact checkpoint/dependency compatibility and AGPL/enterprise
  licensing before selecting: https://docs.ultralytics.com/models/yoloe/
- Standard COCO YOLO: useful bird-detection control, but COCO has no egg or
  chicken-specific class. Not a ready-made egg detector.
- Specialist egg/chicken projects exist, but datasets/papers do not imply
  downloadable usable weights. An inspected specialist chicken model reported
  only 24 training/validation images; treat it as optional, not a strong baseline:
  https://huggingface.co/IceKhoffi/chicken-object-detection-yolov11s

Proposed formal benchmark: annotate around 100–150 diverse frames if available
with `egg` and `chicken` boxes, excluding fully invisible eggs from detection
requirements. Split by day/visit, tune prompts and thresholds only on development
data, then evaluate per-class precision/recall at IoU >= 0.5, AP50, false positives
on empty nests, runtime, and memory on held-out images. A provisional, unapproved
target was >=90% precision and recall per class; sample size limits confidence.
Record exact weights/revisions, dependencies, prompts, preprocessing, and scores.

If zero-shot models are inadequate, human-correct predictions, expand labels,
and fine-tune a small two-class detector. Compare on the same held-out set.
Chicken detection, individual hen identification, tracking, and attribution of
new eggs are separate stages, not solved by the three-basket pre-classifier.
